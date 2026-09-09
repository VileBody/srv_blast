import { useTranslation } from 'react-i18next';
import { Link } from 'react-router-dom';

// Юридические документы уехали на свою страницу (LegalPage) — здесь остались
// только служебные экраны.
type Kind = '404' | 'error';

const HEADING: Record<Kind, string> = {
  '404': 'simple.notFound',
  error: 'simple.error'
};

const TEXT: Record<Kind, string> = {
  '404': 'simple.notFoundText',
  error: 'simple.errorText'
};

export function SimplePage({ kind }: { kind: Kind }) {
  const { t } = useTranslation();
  return (
    <main className="flex min-h-dvh items-center justify-center bg-bg p-[24px] text-center">
      <section className="card-2 flex w-full flex-col items-center justify-center px-[40px] py-[60px]" style={{ maxWidth: 760, minHeight: 420 }}>
        <img src="/assets/figma/logo-star.svg" width="60" height="60" alt="Blast" />
        <h1 className="mt-[32px] text-[36px] font-[400] leading-[43px] text-text">{t(HEADING[kind])}</h1>
        <p className="mt-[16px] max-w-[480px] text-[18px] font-[350] leading-[24px] text-text-60">{t(TEXT[kind])}</p>
        <div className="mt-[32px] flex flex-wrap justify-center gap-[12px]">
          {kind === 'error' && (
            <button type="button" onClick={() => location.reload()} className="flex h-[60px] items-center rounded-r15 bg-grad-main px-[28px] text-[20px] leading-none text-text">
              {t('simple.refresh')}
            </button>
          )}
          <Link className="flex h-[60px] items-center rounded-r15 border border-accent-light bg-grad-soft-20 px-[28px] text-[20px] leading-none text-text-80 transition hover:text-text" to="/app">
            {t('simple.toHome')}
          </Link>
        </div>
      </section>
    </main>
  );
}
