import { FormEvent, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../lib/api';
import { NotchedInput } from '../ui/NotchedInput';
import { useToast } from '../../contexts/ToastContext';

/**
 * Обязательный шаг «представься» после первого входа.
 *
 * Вход идёт только через Telegram и не спрашивает ФИО, а Telegram отдаёт имя не всегда
 * (у аккаунта может не быть ни first_name, ни username). Без этого шага в ЛК оставался
 * пустой профиль, а имя нужно и для договора, и для подписи роликов.
 */
export function ProfileSetupGate({ open }: { open: boolean }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { push } = useToast();
  const [form, setForm] = useState({ name: '', surname: '' });
  const [submitted, setSubmitted] = useState(false);

  const saveMutation = useMutation({
    mutationFn: () => api.updateProfile({ name: form.name.trim(), surname: form.surname.trim() }),
    onSuccess: async () => { await queryClient.invalidateQueries({ queryKey: ['me'] }); },
    onError: (error) => push({ variant: 'error', title: error instanceof Error ? error.message : t('simple.error') })
  });

  if (!open) return null;

  const errors = {
    name: submitted && !form.name.trim() ? t('auth.errName') : undefined,
    surname: submitted && !form.surname.trim() ? t('auth.errSurname') : undefined
  };

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    setSubmitted(true);
    if (!form.name.trim() || !form.surname.trim()) return;
    saveMutation.mutate();
  };

  // Закрыть шаг нельзя (ни крестика, ни клика по подложке): профиль обязателен
  return createPortal(
    <div className="fixed inset-0 z-overlay flex items-center justify-center bg-[rgba(5,1,15,0.86)] p-space-5">
      <form onSubmit={onSubmit} className="w-full max-w-[520px] rounded-r25 bg-card-2 p-[40px]">
        <h2 className="text-[32px] font-[400] leading-[38px] text-text">{t('auth.setupTitle')}</h2>
        <p className="mt-[12px] text-[16px] leading-[21px] text-text-60">{t('auth.setupText')}</p>

        <div className="mt-[40px] flex flex-col gap-[36px]">
          <NotchedInput
            label={t('auth.name')}
            value={form.name}
            onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))}
            error={errors.name}
          />
          <NotchedInput
            label={t('auth.surname')}
            value={form.surname}
            onChange={(event) => setForm((current) => ({ ...current, surname: event.target.value }))}
            error={errors.surname}
          />
        </div>

        <button
          type="submit"
          disabled={saveMutation.isPending}
          className="mt-[36px] flex h-[60px] w-full items-center justify-center rounded-r15 bg-accent text-[20px] font-[400] leading-none text-text transition hover:brightness-110 disabled:opacity-60"
        >
          {saveMutation.isPending ? t('common.loading') : t('auth.setupCta')}
        </button>
      </form>
    </div>,
    document.body
  );
}
