import { useTranslation } from 'react-i18next';
import { api } from '../../lib/api';
import { cn } from '../../lib/cn';
import { FigIcon } from './FigIcon';

/*
 * Кнопка TikTok-аккаунта (Figma: W35 «Статистика» 760:1004, W43/W44 шапка ЛК 747:1583).
 * 240×60 r15, grad-soft-20, backdrop-blur 15; логотип 22×25 + подпись 24 под мягким градиентом.
 * Два состояния одной кнопки: «Подключить» (нет аккаунта) / «Подключен» (есть).
 *
 * Клик по «Подключить» = старт OAuth: уходим ПОЛНЫМ редиректом на бэк (он ведёт на TikTok),
 * потому что авторизация — это уход со страницы, а не fetch. Без ключей бэк мокает подключение.
 */
export function TiktokButton({
  connected,
  onClick,
  className
}: {
  connected: boolean;
  onClick?: () => void;
  className?: string;
}) {
  const { t } = useTranslation();
  const connect = () => { window.location.href = api.tiktokAuthUrl(); };
  return (
    <button
      type="button"
      onClick={onClick ?? (connected ? undefined : connect)}
      className={cn(
        'flex h-[60px] w-[240px] shrink-0 items-center justify-center gap-[15px] rounded-r15 border bg-grad-soft-20 backdrop-blur-[15px] transition hover:brightness-125',
        connected ? 'border-accent' : 'border-transparent',
        className
      )}
    >
      <FigIcon name="tt-logo.svg" h={25} />
      <span
        className="translate-y-px text-[24px] font-[400] leading-none text-transparent"
        style={{
          backgroundImage: 'linear-gradient(184deg, rgba(246,245,253,0.8) 8.5%, rgba(246,245,253,0.64) 94.6%)',
          WebkitBackgroundClip: 'text',
          backgroundClip: 'text'
        }}
      >
        {connected ? t('tiktok.connected') : t('tiktok.connect')}
      </span>
    </button>
  );
}
