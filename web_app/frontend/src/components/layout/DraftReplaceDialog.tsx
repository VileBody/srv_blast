import { useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { useDraftGuard } from '../../stores/draftGuard';
import { ActionBar, Button, Dialog } from '../ui/kit';

/** Вопрос «Заменить текущую настройку?» перед подменой черновика визарда (stores/draftGuard). */
export function DraftReplaceDialog() {
  const { t } = useTranslation();
  const pending = useDraftGuard((state) => state.pending);
  const resolve = useDraftGuard((state) => state.resolve);
  // стабильный колбэк: Dialog переподписывается на onClose и при каждой смене уводил бы фокус
  const keep = useCallback(() => resolve(false), [resolve]);
  return (
    <Dialog
      open={Boolean(pending)}
      onClose={keep}
      title={t('funnel.replaceDraft.title')}
      footer={
        <ActionBar>
          <Button variant="secondary" className="flex-1" onClick={keep}>{t('funnel.replaceDraft.keep')}</Button>
          <Button variant="primary" className="flex-1" onClick={() => resolve(true)}>{t('funnel.replaceDraft.replace')}</Button>
        </ActionBar>
      }
    >
      <p className="text-ui-16 text-text-80 [text-wrap:pretty]">
        {pending?.trackTitle
          ? t('funnel.replaceDraft.text', { track: pending.trackTitle })
          : t('funnel.replaceDraft.textNoTrack')}
      </p>
    </Dialog>
  );
}
