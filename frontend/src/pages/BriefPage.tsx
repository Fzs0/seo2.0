import { DataGuard } from '@/components/StateBlock'
import { useBrief, useKeywords } from '@/hooks/useData'

export function BriefPage() {
  const kw = useKeywords()
  const brief = useBrief(kw.data?.[0]?.id)

  return (
    <section className="page" data-screen-label="Brief 工作台">
      <div>
        <h1>Brief 工作台</h1>
        <p>
          选择一个关键词，系统会基于 seo-standard 中的 articleBriefTemplate、
          anchorTextRules、references 与本地项目配置生成 Brief。
        </p>
      </div>

      <div className="card">
        <div className="card__title">关键词选择</div>
        <div className="filter-row">
          {kw.data?.slice(0, 6).map((k, i) => (
            <span
              key={k.id}
              className={'chip' + (i === 0 ? ' chip--active' : '')}
            >
              <span className="msr">search</span>
              {k.keyword}
            </span>
          ))}
        </div>
      </div>

      <DataGuard
        loading={brief.loading}
        error={brief.error}
        empty={!brief.data}
        emptyTitle="请选择关键词"
        emptyHint="从上方选择一个关键词后生成 Brief"
      >
        {brief.data && (
          <div className="content-grid-2">
            <div className="card">
              <div className="card__title">
                <div className="card__title-icon">
                  <span className="msr">description</span>
                  Brief 内容
                </div>
                <span className="card__title-tag">
                  {brief.data.briefSource === 'ai-enhanced' ? 'AI 增强' : '本地生成'}
                </span>
              </div>
              <pre
                style={{
                  margin: 0,
                  fontFamily: 'inherit',
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                  fontSize: 12.5,
                  color: 'var(--ink-700)',
                  lineHeight: 1.7,
                }}
              >
                {brief.data.brief}
              </pre>
            </div>

            <div className="card">
              <div className="card__title">
                <div className="card__title-icon">
                  <span className="msr">layers</span>
                  结构清单
                </div>
              </div>
              <div className="timeline">
                <Section title="Locale">
                  <TagRow>
                    <span className="tag tag--blue">gl: {brief.data.locale.googleGl}</span>
                    <span className="tag tag--blue">hl: {brief.data.locale.googleHl}</span>
                  </TagRow>
                </Section>

                <Section title="Article Brief Template 模块">
                  <ul
                    style={{
                      margin: 0,
                      padding: 0,
                      listStyle: 'none',
                      display: 'grid',
                      gridTemplateColumns: '1fr 1fr',
                      gap: 8,
                    }}
                  >
                    {brief.data.articleBriefTemplate.map((m) => (
                      <li
                        key={m.name}
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          gap: 8,
                          padding: '6px 10px',
                          borderRadius: 10,
                          border: '1px solid var(--border-soft)',
                          background: 'var(--bg-card-soft)',
                          fontSize: 12,
                        }}
                      >
                        <span
                          className="msr"
                          style={{
                            color: m.required ? 'var(--pink-500)' : 'var(--ink-300)',
                            fontSize: 14,
                          }}
                        >
                          {m.required ? 'check_circle' : 'radio_button_unchecked'}
                        </span>
                        {m.name}
                      </li>
                    ))}
                  </ul>
                </Section>

                <Section title="Image Plan">
                  <ul
                    style={{
                      margin: 0,
                      padding: 0,
                      listStyle: 'none',
                      display: 'flex',
                      flexDirection: 'column',
                      gap: 6,
                    }}
                  >
                    {brief.data.imagePlan.map((img) => (
                      <li
                        key={img.name}
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          gap: 8,
                          fontSize: 12,
                        }}
                      >
                        <span className="msr" style={{ color: 'var(--brand-ink)' }}>
                          image
                        </span>
                        <span className="tbl-strong">{img.name}</span>
                        <span style={{ color: 'var(--ink-400)' }}>· {img.position}</span>
                      </li>
                    ))}
                  </ul>
                </Section>

                {brief.data.reference.triggered && (
                  <Section title="References">
                    <ul
                      style={{
                        margin: 0,
                        padding: 0,
                        listStyle: 'none',
                        display: 'flex',
                        flexDirection: 'column',
                        gap: 6,
                      }}
                    >
                      {brief.data.reference.sources.map((s) => (
                        <li
                          key={s.url}
                          style={{
                            display: 'flex',
                            alignItems: 'center',
                            gap: 8,
                            fontSize: 12,
                          }}
                        >
                          <span className="msr" style={{ color: 'var(--blue-500)' }}>
                            link
                          </span>
                          <span className="tbl-strong">{s.label}</span>
                          <span style={{ color: 'var(--ink-400)' }}>· {s.name}</span>
                        </li>
                      ))}
                    </ul>
                  </Section>
                )}

                <Section title="Target Asset">
                  <div style={{ fontSize: 12 }}>
                    <div style={{ color: 'var(--ink-400)' }}>URL</div>
                    <div className="tbl-strong">{brief.data.targetAsset.url ?? '未指定'}</div>
                    <div
                      style={{
                        display: 'flex',
                        gap: 8,
                        marginTop: 8,
                      }}
                    >
                      <span className="tag tag--gold">
                        状态：{brief.data.targetAsset.status}
                      </span>
                      <span className="tag tag--blue">
                        动作：{brief.data.targetAsset.contentAction}
                      </span>
                    </div>
                  </div>
                </Section>
              </div>
            </div>
          </div>
        )}
      </DataGuard>
    </section>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ marginTop: 14 }}>
      <div
        style={{
          fontSize: 11.5,
          fontWeight: 650,
          color: 'var(--ink-400)',
          marginBottom: 8,
        }}
      >
        {title}
      </div>
      {children}
    </div>
  )
}

function TagRow({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>{children}</div>
  )
}