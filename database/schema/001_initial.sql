-- KDP代理店書籍自動化パイプライン - Supabase (PostgreSQL 15) スキーマ
-- 適用: Supabase SQL Editor または psql で実行

-- ============================================================
-- テーブル定義
-- ============================================================

-- 代理店テーブル
CREATE TABLE IF NOT EXISTS affiliates (
    id          TEXT PRIMARY KEY,              -- 'AF001' 形式（ゼロパディング3桁）
    name        TEXT NOT NULL,                 -- 内部管理名
    display_name TEXT NOT NULL,               -- 書籍著者プロフィール補足表示名
    email       TEXT UNIQUE NOT NULL,          -- ログイン・通知用メールアドレス
    kdp_pen_name TEXT,                         -- KDPでのペンネーム（代理店が設定）
    tracking_url TEXT NOT NULL,               -- https://{COURSE_BASE_URL}/r/{id}
    status      TEXT DEFAULT 'active'
                CHECK (status IN ('active', 'paused', 'suspended')),
    notes       TEXT,                          -- 管理メモ
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

-- 日次生成書籍（マスター）
CREATE TABLE IF NOT EXISTS books (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    date            DATE NOT NULL UNIQUE,       -- 日次で1冊
    topic           TEXT NOT NULL,              -- 書籍タイトル
    subtitle        TEXT,
    topic_category  TEXT,                       -- TOPIC_CATEGORIES のいずれか
    book_plan_json  JSONB NOT NULL,             -- BookPlan全体をJSON保存
    quality_score   NUMERIC(4,2),               -- 5次元スコアのoverall
    status          TEXT DEFAULT 'generating'
                    CHECK (status IN ('generating', 'ready', 'failed')),
    master_epub_url TEXT,                       -- R2/S3 URL
    error_message   TEXT,                       -- 失敗時のエラー詳細
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- 代理店別バリアント
CREATE TABLE IF NOT EXISTS book_variants (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    book_id      UUID NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    affiliate_id TEXT NOT NULL REFERENCES affiliates(id),
    zip_url      TEXT NOT NULL,                 -- R2/S3 URL（ZIPパッケージ）
    epub_url     TEXT,                          -- R2/S3 URL（EPUB単体）
    sha256       TEXT NOT NULL,                 -- ZIPのSHA256（Immutable Artifact原則）
    metadata_json JSONB NOT NULL,              -- KdpMetadata全体
    download_count INTEGER DEFAULT 0,          -- 代理店によるダウンロード回数
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(book_id, affiliate_id)
);

-- クリックログ（書籍内リンク/QR → LP）
CREATE TABLE IF NOT EXISTS attribution_clicks (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    affiliate_id TEXT REFERENCES affiliates(id),
    session_id   UUID NOT NULL,                -- Cookie/URLに埋め込むセッションID
    user_agent   TEXT,
    ip_hash      TEXT,                          -- SHA256ハッシュ（個人情報保護）
    referrer     TEXT,
    fingerprint  TEXT,                          -- ブラウザフィンガープリント（オプション）
    clicked_at   TIMESTAMPTZ DEFAULT NOW()
);

-- コンバージョンログ（講座申込完了）
CREATE TABLE IF NOT EXISTS attribution_conversions (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    affiliate_id TEXT REFERENCES affiliates(id),
    session_id   UUID,                          -- クリックのsession_idと紐付け
    email_hash   TEXT NOT NULL,                -- SHA256ハッシュ（重複防止用）
    course_id    TEXT NOT NULL,                -- 申し込まれた講座ID
    converted_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(email_hash, course_id)              -- 同一メール・同一講座の重複防止
);

-- ============================================================
-- ビュー（集計用）
-- ============================================================

-- 直近7日間の代理店別パフォーマンス
CREATE OR REPLACE VIEW affiliate_stats_7d AS
SELECT
    a.id,
    a.name,
    a.email,
    a.status,
    COUNT(DISTINCT ac.session_id) AS clicks_7d,
    COUNT(DISTINCT co.id) AS conversions_7d,
    ROUND(
        COUNT(DISTINCT co.id)::NUMERIC
        / NULLIF(COUNT(DISTINCT ac.session_id), 0) * 100,
        2
    ) AS cvr_percent
FROM affiliates a
LEFT JOIN attribution_clicks ac
    ON ac.affiliate_id = a.id
    AND ac.clicked_at >= NOW() - INTERVAL '7 days'
LEFT JOIN attribution_conversions co
    ON co.affiliate_id = a.id
    AND co.converted_at >= NOW() - INTERVAL '7 days'
GROUP BY a.id, a.name, a.email, a.status;

-- 今日の書籍ステータス（代理店ポータル表示用）
CREATE OR REPLACE VIEW today_book_status AS
SELECT
    b.id,
    b.date,
    b.topic,
    b.subtitle,
    b.status,
    b.quality_score,
    COUNT(bv.id) AS variant_count
FROM books b
LEFT JOIN book_variants bv ON bv.book_id = b.id
WHERE b.date = CURRENT_DATE
GROUP BY b.id, b.date, b.topic, b.subtitle, b.status, b.quality_score;

-- ============================================================
-- インデックス（クエリパフォーマンス最適化）
-- ============================================================

CREATE INDEX IF NOT EXISTS idx_attribution_clicks_affiliate_date
    ON attribution_clicks(affiliate_id, clicked_at DESC);

CREATE INDEX IF NOT EXISTS idx_attribution_clicks_session
    ON attribution_clicks(session_id);

CREATE INDEX IF NOT EXISTS idx_attribution_conversions_affiliate_date
    ON attribution_conversions(affiliate_id, converted_at DESC);

CREATE INDEX IF NOT EXISTS idx_attribution_conversions_email_hash
    ON attribution_conversions(email_hash);

CREATE INDEX IF NOT EXISTS idx_books_date
    ON books(date DESC);

CREATE INDEX IF NOT EXISTS idx_book_variants_affiliate
    ON book_variants(affiliate_id);

CREATE INDEX IF NOT EXISTS idx_book_variants_book_id
    ON book_variants(book_id);

-- ============================================================
-- Row Level Security (RLS)
-- パイプライン（サービスキー）: 全操作可能
-- 代理店ユーザー（anonキー）: 自分のデータのみ参照可能
-- ============================================================

ALTER TABLE affiliates ENABLE ROW LEVEL SECURITY;
ALTER TABLE book_variants ENABLE ROW LEVEL SECURITY;
ALTER TABLE attribution_clicks ENABLE ROW LEVEL SECURITY;
ALTER TABLE attribution_conversions ENABLE ROW LEVEL SECURITY;

-- 代理店は自分のレコードのみ参照（Supabase Auth emailとaffiliates.emailを照合）
CREATE POLICY "affiliates_self_select" ON affiliates
    FOR SELECT USING (email = auth.jwt() ->> 'email');

CREATE POLICY "book_variants_self_select" ON book_variants
    FOR SELECT USING (
        affiliate_id IN (
            SELECT id FROM affiliates WHERE email = auth.jwt() ->> 'email'
        )
    );

-- クリック/コンバージョンはサービスキーからのみ INSERT（RLSでは全拒否、サービスキーはbypass）
CREATE POLICY "attribution_clicks_deny_all" ON attribution_clicks
    FOR ALL USING (false);

CREATE POLICY "attribution_conversions_deny_all" ON attribution_conversions
    FOR ALL USING (false);

-- ============================================================
-- 更新日時の自動更新トリガー
-- ============================================================

CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER affiliates_updated_at
    BEFORE UPDATE ON affiliates
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER books_updated_at
    BEFORE UPDATE ON books
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ============================================================
-- 初期データ（テスト用代理店）
-- ============================================================

-- INSERT INTO affiliates (id, name, display_name, email, kdp_pen_name, tracking_url)
-- VALUES ('AF001', 'テスト代理店1', '田中太郎', 'test1@example.com', '田中太郎', 'https://yourcourse.jp/r/AF001');
