import { useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router-dom';
import { LanguageSwitcher } from '../components/layout/LanguageSwitcher';
import {
  LEGAL_DOCS,
  LEGAL_UPDATED,
  fillPlaceholders,
  missingRequisites,
  type LegalKind
} from '../data/legal-docs';

/**
 * Публичная страница юридического документа (политика / оферта).
 *
 * Доступна без входа: её URL прикрепляется в consent screen Google и в кабинет
 * разработчика TikTok, и оба проверяют, что страница открывается «с улицы».
 * Язык документа следует языку интерфейса — переключатель тот же, что в приложении.
 */
export function LegalPage({ kind }: { kind: LegalKind }) {
  const { t, i18n } = useTranslation();
  const lang = i18n.language?.slice(0, 2) === 'en' ? 'en' : 'ru';
  const doc = LEGAL_DOCS[lang][kind];
  const other: LegalKind = kind === 'policy' ? 'offer' : 'policy';
  const missing = missingRequisites();

  useEffect(() => {
    // Модерация Google/TikTok смотрит на заголовок вкладки — он должен называть документ
    document.title = `${doc.title} — Blast`;
    document.documentElement.lang = lang;
  }, [doc.title, lang]);

  const updated = new Date(LEGAL_UPDATED).toLocaleDateString(lang === 'en' ? 'en-GB' : 'ru-RU', {
    day: '2-digit',
    month: 'long',
    year: 'numeric'
  });

  return (
    <main className="min-h-dvh bg-bg px-[24px] py-[40px]">
      <div className="mx-auto w-full" style={{ maxWidth: 880 }}>
        <header className="flex flex-wrap items-center justify-between gap-[16px]">
          <Link to="/app" className="flex items-center gap-[12px]">
            <img src="/assets/figma/logo-star.svg" width="40" height="40" alt="Blast" />
            <span className="text-[20px] font-[400] leading-none text-text-80">Blast</span>
          </Link>
          <div className="flex items-center gap-[16px]">
            <LanguageSwitcher />
            <Link
              className="flex h-[44px] items-center rounded-r12 border border-accent-light bg-grad-soft-20 px-[20px] text-[16px] leading-none text-text-80 transition hover:text-text"
              to="/app"
            >
              {t('legal.back')}
            </Link>
          </div>
        </header>

        <article className="card-2 mt-[24px] px-[32px] py-[40px]">
          <h1 className="text-[36px] font-[400] leading-[43px] text-text">{doc.title}</h1>
          <p className="mt-[8px] text-[16px] font-[350] leading-[22px] text-text-40">
            {t('legal.updated', { date: updated })}
          </p>
          <p className="mt-[20px] text-[18px] font-[350] leading-[26px] text-text-80">{doc.intro}</p>

          {missing.length > 0 && (
            <p
              className="mt-[24px] rounded-r12 px-[20px] py-[16px] text-[15px] font-[350] leading-[21px] text-text-80"
              style={{ background: 'var(--accent-10)', boxShadow: 'inset 0 0 0 1px var(--warning)' }}
            >
              {t('legal.requisitesMissing', { fields: missing.join(', ') })}
            </p>
          )}

          <nav className="mt-[28px] border-t border-border pt-[20px]" aria-label={t('legal.contents')}>
            <p className="text-[15px] uppercase tracking-[0.08em] text-text-40">{t('legal.contents')}</p>
            <ol className="mt-[12px] grid gap-[6px] sm:grid-cols-2">
              {doc.sections.map((section, index) => (
                <li key={section.title}>
                  <a
                    href={`#s${index + 1}`}
                    className="text-[16px] font-[350] leading-[22px] text-text-60 transition hover:text-text"
                  >
                    {section.title}
                  </a>
                </li>
              ))}
            </ol>
          </nav>

          <div className="mt-[32px] flex flex-col gap-[32px]">
            {doc.sections.map((section, index) => (
              <section key={section.title} id={`s${index + 1}`} style={{ scrollMarginTop: 24 }}>
                <h2 className="text-[24px] font-[400] leading-[30px] text-text">{section.title}</h2>
                <div className="mt-[12px] flex flex-col gap-[12px]">
                  {section.body.map((block, blockIndex) =>
                    Array.isArray(block) ? (
                      <ul key={blockIndex} className="flex flex-col gap-[8px] pl-[20px]">
                        {block.map((item) => (
                          <li
                            key={item}
                            className="list-disc text-[17px] font-[350] leading-[25px] text-text-80 marker:text-accent"
                          >
                            {fillPlaceholders(item)}
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p key={blockIndex} className="text-[17px] font-[350] leading-[25px] text-text-80">
                        {fillPlaceholders(block)}
                      </p>
                    )
                  )}
                </div>
              </section>
            ))}
          </div>

          <footer className="mt-[40px] flex flex-wrap items-center gap-[12px] border-t border-border pt-[24px]">
            <Link
              className="flex h-[52px] items-center rounded-r15 border border-accent-light bg-grad-soft-20 px-[24px] text-[17px] leading-none text-text-80 transition hover:text-text"
              to={`/legal/${other}`}
            >
              {LEGAL_DOCS[lang][other].title}
            </Link>
          </footer>
        </article>
      </div>
    </main>
  );
}
