import Fastify from 'fastify'
import cors from '@fastify/cors'
import rateLimit from '@fastify/rate-limit'
import cookie from '@fastify/cookie'
import { createClient } from '@supabase/supabase-js'
import { v4 as uuidv4 } from 'uuid'
import crypto from 'crypto'

const PORT = parseInt(process.env.PORT || '3001', 10)
const COURSE_BASE_URL = process.env.COURSE_BASE_URL || 'https://yourcourse.jp'
const ALLOWED_ORIGIN = process.env.ALLOWED_ORIGIN || COURSE_BASE_URL

if (!process.env.SUPABASE_URL || !process.env.SUPABASE_SERVICE_KEY) {
  console.error('SUPABASE_URL と SUPABASE_SERVICE_KEY が必要です')
  process.exit(1)
}

// サービスキー使用（サーバーサイドのみ）
const supabase = createClient(
  process.env.SUPABASE_URL,
  process.env.SUPABASE_SERVICE_KEY,
  { auth: { autoRefreshToken: false, persistSession: false } }
)

const fastify = Fastify({
  logger: { level: process.env.NODE_ENV === 'production' ? 'warn' : 'info' },
  genReqId: () => uuidv4(),
})

// ── プラグイン ────────────────────────────────────────────────

await fastify.register(cors, { origin: ALLOWED_ORIGIN, credentials: true })
await fastify.register(rateLimit, {
  max: 100,
  timeWindow: '1 minute',
  errorResponseBuilder: () => ({ error: 'Too Many Requests', statusCode: 429 }),
})
await fastify.register(cookie, { secret: process.env.COOKIE_SECRET || 'kdp-pipeline-secret' })

// ── ヘルスチェック ────────────────────────────────────────────

fastify.get('/health', async () => ({
  status: 'ok',
  version: '1.0.0',
  ts: new Date().toISOString(),
}))

// ── GET /r/:affiliateId ──────────────────────────────────────
// 書籍内リンク/QRコードからのリダイレクト処理

fastify.get<{ Params: { affiliateId: string }; Querystring: { fp?: string } }>(
  '/r/:affiliateId',
  async (request, reply) => {
    const { affiliateId } = request.params
    const sessionId = uuidv4()
    const defaultLpUrl = `${COURSE_BASE_URL}/lp`

    // 代理店の存在確認
    const { data: affiliate, error: affErr } = await supabase
      .from('affiliates')
      .select('id, status')
      .eq('id', affiliateId)
      .eq('status', 'active')
      .single()

    if (affErr || !affiliate) {
      // 存在しないIDでも訪問者は失わない（デフォルトLPへ）
      request.log.warn({ affiliateId, err: affErr?.message }, 'unknown affiliate, redirecting to default LP')
      return reply.redirect(defaultLpUrl, 302)
    }

    // アトリビューションログを非同期記録（失敗してもリダイレクトは続行）
    const ipHash = crypto
      .createHash('sha256')
      .update(request.ip || 'unknown')
      .digest('hex')

    supabase.from('attribution_clicks').insert({
      affiliate_id: affiliateId,
      session_id: sessionId,
      user_agent: request.headers['user-agent'] || null,
      ip_hash: ipHash,
      referrer: request.headers['referer'] || null,
      fingerprint: request.query.fp || null,
    }).then(({ error }) => {
      if (error) {
        request.log.error({ error: error.message, affiliateId }, 'attribution_clicks insert failed')
      }
    })

    // Cookieセット（30日間）
    reply.setCookie('kdp_aff', affiliateId, {
      path: '/',
      maxAge: 60 * 60 * 24 * 30,
      sameSite: 'lax',
      secure: process.env.NODE_ENV === 'production',
      // httpOnly: false にすることでフロントエンドJSからも読める（フォールバックアトリビューション用）
    })

    // リダイレクト先: LP + ref と sid（Cookieが使えない端末のフォールバック）
    const lpUrl = `${COURSE_BASE_URL}/lp?ref=${affiliateId}&sid=${sessionId}`
    return reply.redirect(lpUrl, 302)
  }
)

// ── POST /api/conversion ─────────────────────────────────────
// 申込完了時にフロントエンドから呼び出す

interface ConversionBody {
  session_id?: string
  ref?: string          // URLパラメータからのfallback
  email_hash: string    // SHA256ハッシュ（生メールは受け取らない）
  course_id: string
}

fastify.post<{ Body: ConversionBody }>('/api/conversion', async (request, reply) => {
  const { session_id, ref, email_hash, course_id } = request.body

  if (!email_hash || !course_id) {
    return reply.status(400).send({ error: 'email_hash と course_id は必須です' })
  }

  // affiliate_id の解決: session_id → attribution_clicks → affiliate_id
  let affiliateId: string | null = null

  if (session_id) {
    const { data } = await supabase
      .from('attribution_clicks')
      .select('affiliate_id')
      .eq('session_id', session_id)
      .limit(1)
      .single()
    affiliateId = data?.affiliate_id ?? null
  }

  // session_idで解決できなかった場合は ref（URLパラメータ）を使用
  if (!affiliateId && ref) {
    affiliateId = ref
  }

  if (!affiliateId) {
    request.log.warn({ session_id, ref }, 'コンバージョンのaffiliate_id解決失敗')
    return reply.status(200).send({ recorded: false, reason: 'no_affiliate_found' })
  }

  // コンバージョン記録（UNIQUE制約で重複は自動的に無視）
  const { error } = await supabase.from('attribution_conversions').insert({
    affiliate_id: affiliateId,
    session_id: session_id ?? null,
    email_hash,
    course_id,
  })

  if (error && error.code !== '23505') {
    // 23505 = unique_violation（重複）は正常、それ以外はエラー
    request.log.error({ error: error.message, affiliateId }, 'attribution_conversions insert failed')
    return reply.status(500).send({ error: 'internal error' })
  }

  return reply.send({ recorded: true, affiliate_id: affiliateId })
})

// ── 起動 ─────────────────────────────────────────────────────

try {
  await fastify.listen({ port: PORT, host: '0.0.0.0' })
  console.log(`redirect-server started on port ${PORT}`)
} catch (err) {
  fastify.log.error(err)
  process.exit(1)
}
