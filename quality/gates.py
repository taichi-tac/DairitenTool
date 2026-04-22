"""品質ゲート - Constitutional Review + 5次元スコアリング"""
import asyncio
import logging
import os
import re
from dataclasses import dataclass
from datetime import date as _date
from typing import Literal

import anthropic

from generators.chapter_generator import Chapter, _count_words

logger = logging.getLogger(__name__)

FORBIDDEN_PHRASES = [
    "必ず稼げます", "誰でも月100万", "すぐに稼げる",
    "他の講座とは違い", "絶対に稼げる", "100%成功",
    "詐欺ではありません", "元手ゼロで",
]

# 規制・BAN対象リスクがある手法（登場したらhard fail）
REGULATED_TACTICS = [
    "アダルト", "成人向けコンテンツ", "18禁",
    "再生数稼ぎ", "再生回数を稼ぐ", "Bot購入", "フォロワー購入",
    "自動いいね", "SEO操作", "大量投稿",
]

# 推奨禁止の時代遅れ手法（章内で登場したらBRAND-003 warn）
OUTDATED_TACTICS = [
    "LINEスタンプ", "ポイントサイト", "アンケートモニター",
    "スマホ内職", "クリック報酬",
]


@dataclass
class ConstitutionalResult:
    passed: bool
    violations: list[str]
    critique: str
    severity: Literal["ok", "warn", "fail"]


@dataclass
class QualityScore:
    accuracy: float      # 0-5: 情報の正確性・根拠の明確さ
    readability: float   # 0-5: 読みやすさ・初心者向け言語
    originality: float   # 0-5: 独自視点・他書との差別化
    value: float         # 0-5: 読者が得られる実用的価値
    brand_safety: float  # 0-5: ブランド安全性
    overall: float       # 加重平均
    passed: bool         # overall >= 4.2
    feedback: str


class QualityGates:
    """
    Constitutional Rules（品質基準）:
    BRAND-001: ブランド毀損コンテンツなし → hard fail
    BRAND-002: 根拠なし断定（禁止フレーズ）なし → hard fail
    LEGAL-001: 長文の直接引用なし → hard fail
    FACT-001: 明らかなハルシネーションなし → warn（自動revise）
    FACT-002: 出典なし数値に断定表現なし → warn
    QUALITY-001: 2,500文字以上 → warn
    QUALITY-002: 具体的ツール名・手順が2箇所以上 → warn
    QUALITY-003: 「今すぐできるアクション」セクション存在 → warn
    """

    def __init__(self) -> None:
        self.client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    # ── ルール規則ベースチェック（LLM不要の高速チェック） ──────────

    def _rule_based_check(self, chapter: Chapter) -> list[tuple[str, str, bool]]:
        """
        返り値: [(rule_id, message, is_hard_fail), ...]
        LLM呼び出し前に即時検出できるルール違反を先に弾く（Shift Left原則）
        """
        violations = []
        content = chapter.content

        # BRAND-001: 規制対象・BAN対象手法チェック → hard fail
        for phrase in REGULATED_TACTICS:
            if phrase in content:
                violations.append(("BRAND-001", f"規制対象手法「{phrase}」が含まれています。アカウントBANリスクがあり即時却下", True))

        # BRAND-002: 禁止フレーズチェック（ハードコードされた規則のため高速）
        for phrase in FORBIDDEN_PHRASES:
            if phrase in content:
                violations.append(("BRAND-002", f"禁止フレーズ「{phrase}」が含まれています", True))

        # BRAND-003: 時代遅れ手法の推奨チェック
        for tactic in OUTDATED_TACTICS:
            if tactic in content:
                violations.append(("BRAND-003", f"時代遅れ手法「{tactic}」が含まれています。推奨内容でないか確認してください", False))

        # QUALITY-001: 文字数チェック
        if _count_words(content) < 2500:
            violations.append(("QUALITY-001", f"文字数不足: {_count_words(content)}文字 (最低2500文字)", False))

        # QUALITY-003: アクションセクション存在チェック
        if "今すぐできるアクション" not in content and "アクションステップ" not in content:
            violations.append(("QUALITY-003", "「今すぐできるアクション」セクションがありません", False))

        # LEGAL-001: 長文引用チェック（400文字以上の引用ブロック）
        blockquotes = re.findall(r'^>(.+)$', content, re.MULTILINE)
        long_quotes = [q for q in blockquotes if len(q) > 400]
        if long_quotes:
            violations.append(("LEGAL-001", f"長文引用（400文字超）が{len(long_quotes)}箇所あります", True))

        return violations

    # ── LLMベースのConstitutional Review ────────────────────────────

    def constitutional_review(self, chapter: Chapter, current_date: str | None = None) -> ConstitutionalResult:
        """Constitutional AI原則でレビュー"""
        # まずルールベースチェック（高速）
        rule_violations = self._rule_based_check(chapter)
        hard_fails = [v for v in rule_violations if v[2]]
        warns = [v for v in rule_violations if not v[2]]

        if hard_fails:
            msgs = [f"{v[0]}: {v[1]}" for v in hard_fails]
            return ConstitutionalResult(
                passed=False, violations=msgs, critique="\n".join(msgs), severity="fail"
            )

        date_ctx = current_date or _date.today().isoformat()
        year_ctx = date_ctx[:4]

        # LLMによる詳細チェック（FACT-001, FACT-002, QUALITY-002）
        prompt = f"""以下の書籍の章を品質審査してください。

## 重要：現在の日付
本日は{date_ctx}（{year_ctx}年）です。
{year_ctx}年の情報は「現時点の最新情報」です。「未来の情報」「検証不能な未来予測」として扱わないでください。
トレーニングデータにない{year_ctx}年の具体的な製品名・事例は、記事として収集された実在の情報です。

## 審査ルール
FACT-001: 明らかなハルシネーション（存在しない企業・ツール・実在しないバージョン番号）がないか
          ※ {year_ctx}年の出来事であることは理由にしない。製品名・バージョンの不自然さのみ判断する
FACT-002: 数値・統計・企業事例に「（出典: ○○）」または「〜とも言われています」の表現があるか
QUALITY-002: 具体的なツール名（ChatGPT、Claudeなど）が2箇所以上あるか

## レビュー対象（第{chapter.number}章）
{chapter.content[:3000]}...（以下省略）

## 出力形式（JSONのみ）
{{
  "violations": ["違反ルールID: 説明", ...],  // なければ空配列
  "critique": "改善のための具体的な指示（violationsがある場合）",
  "should_revise": true/false
}}"""

        response = self.client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]

        import json
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("ConstitutionalReview JSONパース失敗、passとして続行")
            data = {"violations": [], "critique": "", "should_revise": False}

        all_violations = [v[1] for v in warns] + data.get("violations", [])
        severity: Literal["ok", "warn", "fail"] = "ok"
        if all_violations:
            severity = "warn"

        return ConstitutionalResult(
            passed=not data.get("should_revise", False) and not warns,
            violations=all_violations,
            critique=data.get("critique", ""),
            severity=severity,
        )

    def revise(self, chapter: Chapter, critique: str, revision_num: int = 1) -> Chapter:
        """critiqueを反映して章を修正（最大2回まで呼ばれる）"""
        logger.info("第%d章修正中 (revision #%d)", chapter.number, revision_num)
        prompt = f"""以下の指摘事項を全て修正して、章を書き直してください。

## 指摘事項
{critique}

## 元の章
{chapter.content}

修正した完全な章をMarkdown形式で出力してください（説明文なし）。"""

        response = self.client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=8000,
            messages=[{"role": "user", "content": prompt}],
        )
        new_content = response.content[0].text

        from generators.chapter_generator import _check_action_steps, _check_tool_recommendations
        return Chapter(
            number=chapter.number,
            title=chapter.title,
            content=new_content,
            word_count=_count_words(new_content),
            has_action_steps=_check_action_steps(new_content),
            has_tool_recommendations=_check_tool_recommendations(new_content),
        )

    # ── 5次元スコアリング（3エージェント並列） ───────────────────────

    async def _score_by_reviewer(self, chapter: Chapter, focus: str,
                                  dimensions: list[str],
                                  current_date: str | None = None) -> dict[str, float]:
        """1レビュアーによるスコアリング"""
        date_ctx = current_date or _date.today().isoformat()
        year_ctx = date_ctx[:4]
        dims_str = "\n".join(f"- {d}" for d in dimensions)
        prompt = f"""書籍の章を以下の観点で採点してください。

## 重要：現在の日付
本日は{date_ctx}（{year_ctx}年）です。{year_ctx}年の情報は現時点の最新情報です。
年号を理由に減点しないでください。

## あなたの役割
{focus}

## 採点対象の観点（各5点満点）
{dims_str}

## 採点対象（第{chapter.number}章の最初3000文字）
{chapter.content[:3000]}

## 出力形式（JSONのみ）
{{"scores": {{{", ".join(f'"{d.split(":")[0].strip().lower()}": 0.0' for d in dimensions)}}}, "feedback": "改善コメント（1文）"}}"""

        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: self.client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=400,
                messages=[{"role": "user", "content": prompt}],
            ),
        )
        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        import json
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"scores": {d.split(":")[0].strip().lower(): 3.5 for d in dimensions}, "feedback": ""}

    async def score(self, chapter: Chapter, current_date: str | None = None) -> QualityScore:
        """3エージェント並列で5次元スコアリング"""
        r1, r2, r3 = await asyncio.gather(
            self._score_by_reviewer(chapter, "事実確認専門家", ["accuracy: 情報の正確性・根拠の明確さ", "brand_safety: ブランド安全性"], current_date),
            self._score_by_reviewer(chapter, "読者体験アナリスト", ["readability: 読みやすさ・初心者向け", "value: 読者が得られる実用的価値"], current_date),
            self._score_by_reviewer(chapter, "コンテンツ独自性審査員", ["originality: 独自視点・他書との差別化"], current_date),
        )

        def avg(key: str, *results: dict) -> float:
            vals = [r.get("scores", {}).get(key, 3.5) for r in results if key in r.get("scores", {})]
            return round(sum(vals) / len(vals), 2) if vals else 3.5

        accuracy = avg("accuracy", r1)
        readability = avg("readability", r2)
        originality = avg("originality", r3)
        value = avg("value", r2)
        brand_safety = avg("brand_safety", r1)

        overall = round(
            accuracy * 0.25 + readability * 0.20 + originality * 0.20 + value * 0.25 + brand_safety * 0.10,
            2,
        )
        feedbacks = [r.get("feedback", "") for r in [r1, r2, r3] if r.get("feedback")]
        return QualityScore(
            accuracy=accuracy,
            readability=readability,
            originality=originality,
            value=value,
            brand_safety=brand_safety,
            overall=overall,
            passed=overall >= 4.2,
            feedback=" | ".join(feedbacks[:2]),
        )

    def run_gate(self, chapter: Chapter, max_revisions: int = 2,
                 current_date: str | None = None) -> tuple[Chapter, QualityScore]:
        """フル品質チェック: constitutional_review → revise(必要時) → score"""
        current = chapter
        for rev in range(max_revisions + 1):
            result = self.constitutional_review(current, current_date)
            if result.severity == "fail":
                logger.error("第%d章 Constitutional hard fail: %s", chapter.number, result.violations)
                raise ValueError(f"Chapter {chapter.number} hard fail: {result.violations}")
            if result.severity == "ok" or rev >= max_revisions:
                break
            logger.info("第%d章 revise #%d 実行", chapter.number, rev + 1)
            current = self.revise(current, result.critique, rev + 1)

        score = asyncio.run(self.score(current, current_date))
        logger.info("第%d章スコア: overall=%.2f passed=%s", chapter.number, score.overall, score.passed)
        return current, score
