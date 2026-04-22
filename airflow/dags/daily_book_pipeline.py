"""日次書籍生成DAG - 毎日AM2時(JST)に実行"""
import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from airflow import DAG
from airflow.models import Variable
from airflow.operators.python import PythonOperator

logger = logging.getLogger(__name__)

OUTPUT_BASE = Path(os.environ.get("OUTPUT_DIR", "/tmp/kdp-output"))


# ── Slack通知 ────────────────────────────────────────────────

def slack_failure_alert(context: dict) -> None:
    """タスク失敗時にSlack通知（サイレント失敗禁止）"""
    webhook_url = Variable.get("SLACK_WEBHOOK_URL", default_var=None)
    if not webhook_url:
        return
    task_id = context.get("task_instance", {}).task_id if hasattr(context.get("task_instance", {}), "task_id") else "unknown"
    dag_id = context.get("dag", {}).dag_id if hasattr(context.get("dag", {}), "dag_id") else "unknown"
    msg = {
        "text": f":x: *KDP Pipeline失敗*\n DAG: `{dag_id}` / Task: `{task_id}`\n"
                f"実行日時: {datetime.now(timezone.utc).isoformat()}"
    }
    try:
        import urllib.request
        req = urllib.request.Request(
            webhook_url,
            data=json.dumps(msg).encode(),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        logger.error("Slack通知失敗: %s", e)


# ── タスク関数 ───────────────────────────────────────────────

def validate_env(**kwargs) -> None:
    """必要な環境変数・APIキーの存在確認（Shift Left原則: 早期失敗）"""
    required_vars = ["ANTHROPIC_API_KEY", "SUPABASE_URL", "SUPABASE_SERVICE_KEY"]
    missing = []
    for var in required_vars:
        val = Variable.get(var, default_var=os.environ.get(var))
        if not val:
            missing.append(var)
        else:
            os.environ[var] = val  # 後続タスクから参照できるようにセット
    if missing:
        raise ValueError(f"必要な環境変数が未設定: {missing}")

    # 任意変数（欠けていても続行するが警告を出す）
    optional_vars = ["OPENAI_API_KEY", "RESEND_API_KEY", "SLACK_WEBHOOK_URL"]
    for var in optional_vars:
        val = Variable.get(var, default_var=os.environ.get(var))
        if val:
            os.environ[var] = val
        else:
            logger.warning("任意変数 %s が未設定です（一部機能が使えません）", var)


def fetch_sources(execution_date: str, **kwargs) -> dict:
    """情報収集: IngestionAgentを実行して記事を取得"""
    from agents.ingestion_agent import IngestionAgent
    from agents.topic_planner import _get_topic_category

    topic_category = _get_topic_category(execution_date)
    agent = IngestionAgent()
    articles = asyncio.run(agent.run(topic=topic_category, date=execution_date))

    # XComにはシリアライズ可能な形式で渡す
    return {
        "articles": [
            {
                "title": a.title, "url": a.url, "summary": a.summary,
                "source": a.source, "trust_score": a.trust_score,
                "language": a.language, "relevance_score": a.relevance_score,
            }
            for a in articles
        ],
        "topic_category": topic_category,
    }


def plan_topic(execution_date: str, **kwargs) -> dict:
    """書籍計画の生成: TopicPlannerを実行"""
    ti = kwargs["ti"]
    fetch_result = ti.xcom_pull(task_ids="fetch_sources")
    from agents.ingestion_agent import Article
    from agents.topic_planner import TopicPlanner
    from datetime import datetime as dt

    articles = [
        Article(
            title=a["title"], url=a["url"], summary=a["summary"],
            published_at=dt.now(timezone.utc),
            source=a["source"], trust_score=a["trust_score"],
            language=a["language"], relevance_score=a["relevance_score"],
        )
        for a in fetch_result["articles"]
    ]

    planner = TopicPlanner()
    book_plan = planner.plan(execution_date, articles, fetch_result["topic_category"])

    return {
        "date": book_plan.date,
        "topic": book_plan.topic,
        "subtitle": book_plan.subtitle,
        "target_reader": book_plan.target_reader,
        "topic_category": book_plan.topic_category,
        "keywords": book_plan.keywords,
        "category_1": book_plan.category_1,
        "category_2": book_plan.category_2,
        "estimated_word_count": book_plan.estimated_word_count,
        "chapters": [
            {"number": c.number, "title": c.title, "key_points": c.key_points, "target_words": c.target_words}
            for c in book_plan.chapters
        ],
    }


def generate_chapters(execution_date: str, **kwargs) -> list[dict]:
    """章生成: ChapterGeneratorを実行（最大3章並列）"""
    ti = kwargs["ti"]
    plan_data = ti.xcom_pull(task_ids="plan_topic")
    fetch_result = ti.xcom_pull(task_ids="fetch_sources")

    from agents.ingestion_agent import Article
    from agents.topic_planner import BookPlan, ChapterOutline
    from generators.chapter_generator import ChapterGenerator
    from datetime import datetime as dt

    articles = [
        Article(
            title=a["title"], url=a["url"], summary=a["summary"],
            published_at=dt.now(timezone.utc),
            source=a["source"], trust_score=a["trust_score"],
            language=a["language"],
        )
        for a in fetch_result["articles"]
    ]
    book_plan = BookPlan(
        date=plan_data["date"],
        topic=plan_data["topic"],
        subtitle=plan_data["subtitle"],
        target_reader=plan_data["target_reader"],
        topic_category=plan_data["topic_category"],
        keywords=plan_data["keywords"],
        category_1=plan_data["category_1"],
        category_2=plan_data["category_2"],
        estimated_word_count=plan_data["estimated_word_count"],
        chapters=[ChapterOutline(**c) for c in plan_data["chapters"]],
    )

    gen = ChapterGenerator()
    chapters = asyncio.run(gen.generate_all(book_plan, articles))
    return [
        {"number": c.number, "title": c.title, "content": c.content,
         "word_count": c.word_count, "has_action_steps": c.has_action_steps,
         "has_tool_recommendations": c.has_tool_recommendations}
        for c in chapters
    ]


def run_quality_gates(execution_date: str, **kwargs) -> dict:
    """品質ゲート: Constitutional Review + 5次元スコアリング"""
    ti = kwargs["ti"]
    chapters_data = ti.xcom_pull(task_ids="generate_chapters")

    from generators.chapter_generator import Chapter
    from quality.gates import QualityGates

    gates = QualityGates()
    results = []
    total_score = 0.0

    for ch_data in chapters_data:
        chapter = Chapter(**ch_data)
        try:
            revised_chapter, score = gates.run_gate(chapter)
            results.append({
                "number": revised_chapter.number,
                "title": revised_chapter.title,
                "content": revised_chapter.content,
                "word_count": revised_chapter.word_count,
                "has_action_steps": revised_chapter.has_action_steps,
                "has_tool_recommendations": revised_chapter.has_tool_recommendations,
                "quality_score": score.overall,
                "passed": score.passed,
            })
            total_score += score.overall
        except ValueError as e:
            # hard fail章はSlackに通知して章をスキップ
            logger.error("章 %d hard fail: %s", ch_data["number"], e)
            slack_failure_alert({"task_instance": type("T", (), {"task_id": f"chapter_{ch_data['number']}_hard_fail"})(), "dag": type("D", (), {"dag_id": "daily_book_pipeline"})()})

    avg_score = total_score / len(results) if results else 0
    return {"chapters": results, "avg_quality_score": round(avg_score, 2)}


def build_epub(execution_date: str, **kwargs) -> str:
    """EPUB生成（マスター版・アフィリエイトID=MASTER）"""
    ti = kwargs["ti"]
    gate_result = ti.xcom_pull(task_ids="run_quality_gates")
    plan_data = ti.xcom_pull(task_ids="plan_topic")

    from agents.topic_planner import BookPlan, ChapterOutline
    from generators.chapter_generator import Chapter
    from generators.epub_builder import Affiliate, EpubBuilder

    book_plan = BookPlan(
        date=plan_data["date"], topic=plan_data["topic"], subtitle=plan_data["subtitle"],
        target_reader=plan_data["target_reader"], topic_category=plan_data["topic_category"],
        keywords=plan_data["keywords"], category_1=plan_data["category_1"],
        category_2=plan_data["category_2"], estimated_word_count=plan_data["estimated_word_count"],
        chapters=[ChapterOutline(**c) for c in plan_data["chapters"]],
    )
    chapters = [Chapter(**c) for c in gate_result["chapters"]]

    # マスター用プレースホルダー代理店（CTAはEpubBuilder内で置換される）
    master_affiliate = Affiliate(
        id="MASTER", name="Master", display_name="",
        tracking_url="[COURSE_CTA_PLACEHOLDER]",
        email="", kdp_pen_name="著者",
    )

    output_dir = OUTPUT_BASE / execution_date / "epub"
    builder = EpubBuilder()
    epub_path = builder.build(chapters, book_plan, master_affiliate, output_dir)
    return str(epub_path)


def build_kdp_packages(execution_date: str, **kwargs) -> list[str]:
    """代理店全員分のKDPパッケージを並列生成"""
    ti = kwargs["ti"]
    plan_data = ti.xcom_pull(task_ids="plan_topic")
    gate_result = ti.xcom_pull(task_ids="run_quality_gates")

    from agents.topic_planner import BookPlan, ChapterOutline
    from generators.chapter_generator import Chapter
    from generators.epub_builder import Affiliate
    from publishers.kdp_package_builder import KdpPackageBuilder
    from supabase import create_client

    supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    aff_res = supabase.table("affiliates").select("id,name,display_name,email,kdp_pen_name,tracking_url").eq("status", "active").execute()

    affiliates = [
        Affiliate(
            id=a["id"], name=a["name"], display_name=a["display_name"],
            tracking_url=a["tracking_url"], email=a["email"],
            kdp_pen_name=a["kdp_pen_name"] or a["display_name"],
        )
        for a in (aff_res.data or [])
    ]

    if not affiliates:
        logger.warning("アクティブな代理店が0件です")
        return []

    book_plan = BookPlan(
        date=plan_data["date"], topic=plan_data["topic"], subtitle=plan_data["subtitle"],
        target_reader=plan_data["target_reader"], topic_category=plan_data["topic_category"],
        keywords=plan_data["keywords"], category_1=plan_data["category_1"],
        category_2=plan_data["category_2"], estimated_word_count=plan_data["estimated_word_count"],
        chapters=[ChapterOutline(**c) for c in plan_data["chapters"]],
    )
    chapters = [Chapter(**c) for c in gate_result["chapters"]]

    output_dir = OUTPUT_BASE / execution_date / "packages"
    builder = KdpPackageBuilder()
    packages = builder.build_all_variants(
        cover_image=None,  # カバー画像なしでも動作する
        book_plan=book_plan,
        chapters=chapters,
        affiliates=affiliates,
        output_dir=output_dir,
    )
    return [str(p.zip_path) for p in packages]


def notify_affiliates(execution_date: str, **kwargs) -> None:
    """代理店にメールで書籍完成を通知"""
    ti = kwargs["ti"]
    pkg_paths = ti.xcom_pull(task_ids="build_kdp_packages")
    plan_data = ti.xcom_pull(task_ids="plan_topic")

    resend_api_key = os.environ.get("RESEND_API_KEY")
    from_email = os.environ.get("NOTIFICATION_FROM_EMAIL", "noreply@yourcourse.jp")

    if not resend_api_key:
        logger.warning("RESEND_API_KEY未設定のためメール通知をスキップします")
        return

    import resend
    resend.api_key = resend_api_key

    from supabase import create_client
    supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    aff_res = supabase.table("affiliates").select("id,name,email").eq("status", "active").execute()

    for aff in (aff_res.data or []):
        try:
            portal_url = os.environ.get("COURSE_BASE_URL", "https://yourcourse.jp") + "/portal"
            resend.Emails.send({
                "from": from_email,
                "to": aff["email"],
                "subject": f"📚 本日の書籍が完成しました！「{plan_data['topic']}」",
                "html": f"""
<p>{aff['name']} さん、こんにちは！</p>
<p>本日（{execution_date}）の書籍が完成しました。</p>
<p><strong>「{plan_data['topic']}」</strong></p>
<p>代理店ポータルからKDPパッケージをダウンロードして、今日中に出版しましょう！</p>
<p><a href="{portal_url}" style="background:#1a73e8;color:white;padding:12px 24px;border-radius:8px;text-decoration:none;display:inline-block">
  ポータルでダウンロード
</a></p>
<p style="color:#888;font-size:12px">このメールは自動送信されています。</p>
""",
            })
            logger.info("通知メール送信完了: %s", aff["email"])
        except Exception as e:
            # 1件の通知失敗は他の代理店に影響しない
            logger.error("メール送信失敗 email=%s err=%s", aff["email"], e)


# ── DAG定義 ──────────────────────────────────────────────────

default_args = {
    "owner": "kdp-pipeline",
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
    "on_failure_callback": slack_failure_alert,
    "execution_timeout": timedelta(hours=3),
}

with DAG(
    dag_id="daily_book_pipeline",
    default_args=default_args,
    schedule="0 17 * * *",         # UTC 17:00 = JST 02:00
    start_date=datetime(2026, 4, 22),
    catchup=False,
    tags=["kdp", "production"],
    doc_md="""
    ## KDP日次書籍生成パイプライン
    海外AIニュースを収集し、Claude APIでAI副業電子書籍を自動生成。
    代理店ごとのKDPパッケージを作成してメールで通知する。
    """,
) as dag:

    t_validate = PythonOperator(
        task_id="validate_env",
        python_callable=validate_env,
        op_kwargs={"execution_date": "{{ ds }}"},
    )

    t_fetch = PythonOperator(
        task_id="fetch_sources",
        python_callable=fetch_sources,
        op_kwargs={"execution_date": "{{ ds }}"},
    )

    t_plan = PythonOperator(
        task_id="plan_topic",
        python_callable=plan_topic,
        op_kwargs={"execution_date": "{{ ds }}"},
    )

    t_generate = PythonOperator(
        task_id="generate_chapters",
        python_callable=generate_chapters,
        op_kwargs={"execution_date": "{{ ds }}"},
        retries=3,
        retry_delay=timedelta(minutes=10),
    )

    t_quality = PythonOperator(
        task_id="run_quality_gates",
        python_callable=run_quality_gates,
        op_kwargs={"execution_date": "{{ ds }}"},
    )

    t_epub = PythonOperator(
        task_id="build_epub",
        python_callable=build_epub,
        op_kwargs={"execution_date": "{{ ds }}"},
    )

    t_packages = PythonOperator(
        task_id="build_kdp_packages",
        python_callable=build_kdp_packages,
        op_kwargs={"execution_date": "{{ ds }}"},
    )

    t_notify = PythonOperator(
        task_id="notify_affiliates",
        python_callable=notify_affiliates,
        op_kwargs={"execution_date": "{{ ds }}"},
    )

    # DAG依存関係（線形チェーン）
    t_validate >> t_fetch >> t_plan >> t_generate >> t_quality >> t_epub >> t_packages >> t_notify
