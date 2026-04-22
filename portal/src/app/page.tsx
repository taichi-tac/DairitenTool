import { redirect } from 'next/navigation'
import { createServerSupabase } from '@/lib/supabase'

interface BookVariant {
  id: string
  zip_url: string
  created_at: string
  books: { date: string; topic: string; status: string; quality_score: number | null }
}

interface Stats {
  clicks_7d: number
  conversions_7d: number
  cvr_percent: number
}

async function getAffiliate(email: string) {
  const supabase = createServerSupabase()
  const { data } = await supabase
    .from('affiliates')
    .select('id, name, display_name, tracking_url')
    .eq('email', email)
    .single()
  return data
}

async function getRecentVariants(affiliateId: string): Promise<BookVariant[]> {
  const supabase = createServerSupabase()
  const { data } = await supabase
    .from('book_variants')
    .select('id, zip_url, created_at, books(date, topic, status, quality_score)')
    .eq('affiliate_id', affiliateId)
    .order('created_at', { ascending: false })
    .limit(14)
  return (data as BookVariant[]) ?? []
}

async function getStats(affiliateId: string): Promise<Stats> {
  const supabase = createServerSupabase()
  const { data } = await supabase
    .from('affiliate_stats_7d')
    .select('clicks_7d, conversions_7d, cvr_percent')
    .eq('id', affiliateId)
    .single()
  return data ?? { clicks_7d: 0, conversions_7d: 0, cvr_percent: 0 }
}

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, { label: string; cls: string }> = {
    ready: { label: '✅ 準備完了', cls: 'bg-green-100 text-green-800' },
    generating: { label: '🔄 生成中', cls: 'bg-yellow-100 text-yellow-800' },
    failed: { label: '❌ エラー', cls: 'bg-red-100 text-red-800' },
  }
  const { label, cls } = map[status] ?? { label: status, cls: 'bg-gray-100 text-gray-800' }
  return (
    <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${cls}`}>{label}</span>
  )
}

export default async function DashboardPage() {
  const supabase = createServerSupabase()
  const { data: { user } } = await supabase.auth.getUser()

  if (!user?.email) {
    redirect('/login')
  }

  const affiliate = await getAffiliate(user.email)
  if (!affiliate) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <p className="text-gray-500">代理店アカウントが見つかりません。担当者にお問い合わせください。</p>
      </div>
    )
  }

  const [variants, stats] = await Promise.all([
    getRecentVariants(affiliate.id),
    getStats(affiliate.id),
  ])

  const todayVariant = variants.find(v => v.books?.date === new Date().toISOString().split('T')[0])

  return (
    <div className="min-h-screen bg-gray-50">
      <header className="bg-white border-b px-6 py-4">
        <div className="max-w-4xl mx-auto flex items-center justify-between">
          <h1 className="text-lg font-bold text-gray-800">📚 代理店ポータル</h1>
          <span className="text-sm text-gray-500">{affiliate.display_name} さん</span>
        </div>
      </header>

      <main className="max-w-4xl mx-auto px-6 py-8 space-y-6">

        {/* 今日の書籍 */}
        <section className="bg-white rounded-xl shadow-sm p-6">
          <h2 className="text-base font-semibold text-gray-700 mb-4">今日の書籍</h2>
          {todayVariant ? (
            <div className="flex items-center justify-between gap-4">
              <div>
                <StatusBadge status={todayVariant.books?.status ?? 'generating'} />
                <p className="mt-2 font-medium text-gray-800">{todayVariant.books?.topic}</p>
                {todayVariant.books?.quality_score && (
                  <p className="text-sm text-gray-500">品質スコア: {todayVariant.books.quality_score}/5.0</p>
                )}
              </div>
              {todayVariant.books?.status === 'ready' && (
                <a
                  href={todayVariant.zip_url}
                  className="shrink-0 bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded-lg text-sm font-medium"
                  download
                >
                  KDPパッケージをダウンロード
                </a>
              )}
            </div>
          ) : (
            <p className="text-gray-500 text-sm">今日の書籍を生成中です。AM 4時ごろに完成予定です。</p>
          )}
        </section>

        {/* 直近7日間の統計 */}
        <section className="bg-white rounded-xl shadow-sm p-6">
          <h2 className="text-base font-semibold text-gray-700 mb-4">直近7日間の実績</h2>
          <div className="grid grid-cols-3 gap-4 text-center">
            <div>
              <p className="text-3xl font-bold text-blue-600">{stats.clicks_7d}</p>
              <p className="text-sm text-gray-500 mt-1">クリック数</p>
            </div>
            <div>
              <p className="text-3xl font-bold text-green-600">{stats.conversions_7d}</p>
              <p className="text-sm text-gray-500 mt-1">申込数</p>
            </div>
            <div>
              <p className="text-3xl font-bold text-purple-600">{stats.cvr_percent}%</p>
              <p className="text-sm text-gray-500 mt-1">CVR</p>
            </div>
          </div>
        </section>

        {/* 書籍履歴 */}
        <section className="bg-white rounded-xl shadow-sm p-6">
          <h2 className="text-base font-semibold text-gray-700 mb-4">書籍履歴（直近14日）</h2>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-gray-500">
                  <th className="text-left py-2 pr-4">日付</th>
                  <th className="text-left py-2 pr-4">タイトル</th>
                  <th className="text-left py-2 pr-4">状態</th>
                  <th className="text-left py-2">ダウンロード</th>
                </tr>
              </thead>
              <tbody>
                {variants.map(v => (
                  <tr key={v.id} className="border-b last:border-0">
                    <td className="py-2 pr-4 text-gray-500 whitespace-nowrap">{v.books?.date}</td>
                    <td className="py-2 pr-4 text-gray-800">{v.books?.topic?.slice(0, 40)}...</td>
                    <td className="py-2 pr-4"><StatusBadge status={v.books?.status ?? ''} /></td>
                    <td className="py-2">
                      {v.books?.status === 'ready' && (
                        <a href={v.zip_url} className="text-blue-600 hover:underline" download>
                          ZIP
                        </a>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        {/* トラッキング情報 */}
        <section className="bg-white rounded-xl shadow-sm p-6">
          <h2 className="text-base font-semibold text-gray-700 mb-2">あなたのトラッキングURL</h2>
          <p className="text-gray-500 text-sm mb-3">
            書籍内のリンク・QRコードはこのURLで追跡されています。
          </p>
          <code className="block bg-gray-100 rounded px-3 py-2 text-sm break-all">
            {affiliate.tracking_url}
          </code>
        </section>

      </main>
    </div>
  )
}
