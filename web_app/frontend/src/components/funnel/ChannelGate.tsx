import { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQueryClient } from '@tanstack/react-query';
import { api } from '../../lib/api';
import { Button, ButtonLink, Icon } from '../ui/kit';
import { FunnelDialog, FunnelSheet } from './FunnelSheet';
import { FN_GLYPH, type ActionStatus } from './parts';

/*
 * Гейт сабмита на сайте: бесплатные генерации — только подписчикам канала (сервер
 * отвечает 403 channel_subscription_required). Окно ведёт в канал и перепроверяет
 * подписку по «Я подписался»; подтвердилась — onPassed (визард запускает генерацию
 * тем же ключом идемпотентности). Сбой проверки — не «не подписан»: просим повторить.
 */
export function ChannelGate({ channelLink, onClose, onPassed }: { channelLink: string; onClose: () => void; onPassed: () => void }) {
  const { t } = useTranslation();
  const titleId = useId();
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<ActionStatus>('todo');
  const check = () => {
    setStatus('checking');
    api.funnelChannel()
      .then((res) => {
        if (!res.subscribed) {
          setStatus('missing');
          return;
        }
        setStatus('done');
        void queryClient.invalidateQueries({ queryKey: ['funnel-state'] });
        onPassed();
      })
      .catch(() => setStatus('error'));
  };
  const note = status === 'missing' ? t('funnel.actions.channelMissing')
    : status === 'error' ? t('funnel.actions.channelCheckFailed')
      : null;
  return (
    <FunnelDialog open onClose={onClose} labelledBy={titleId}>
      <FunnelSheet
        titleId={titleId}
        stepKey="channel-gate"
        onClose={onClose}
        title={t('funnel.channelGate.title')}
        description={t('funnel.channelGate.description')}
        actions={
          <>
            <ButtonLink variant="secondary" href={channelLink} target="_blank" rel="noreferrer" icon={<Icon>{FN_GLYPH.send}</Icon>}>
              {t('funnel.actions.channelOpen')}
            </ButtonLink>
            <Button variant="primary" loading={status === 'checking'} onClick={check}>
              {t('funnel.channelGate.subscribed')}
            </Button>
          </>
        }
      >
        {note && <p className="text-ui-14 text-warning [text-wrap:pretty]" role="status">{note}</p>}
      </FunnelSheet>
    </FunnelDialog>
  );
}
