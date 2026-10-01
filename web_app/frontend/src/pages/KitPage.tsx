import { useState } from 'react';
import {
  ActionBar,
  Button,
  Checkbox,
  Dialog,
  DropZone,
  GLYPH,
  Icon,
  Pager,
  Pill,
  SectionHeader,
  Segmented,
  Surface,
  Switch,
  Tag,
  TextField
} from '../components/ui/kit';

/*
 * Витрина общих компонентов единой шкалы (UI_RULES.md). Только dev-сборка (/dev/kit):
 * все состояния рядом, чтобы переводимые экраны сверялись с одним образцом.
 * Тексты здесь — служебные примеры, в словари не выносятся.
 */
function Block({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Surface level="card" className="flex flex-col gap-[16px] p-[24px]">
      <SectionHeader title={title} />
      {children}
    </Surface>
  );
}

const Row = ({ children, top = false }: { children: React.ReactNode; top?: boolean }) => (
  <div className={`flex flex-wrap gap-[12px] ${top ? 'items-start' : 'items-center'}`}>{children}</div>
);

export function KitPage() {
  const [stage, setStage] = useState('bg');
  const [batch, setBatch] = useState('1');
  const [type, setType] = useState('vertical');
  const [rate, setRate] = useState<string | null>(null);
  const [page, setPage] = useState(0);
  const [pills, setPills] = useState<string[]>(['night']);
  const [agree, setAgree] = useState(false);
  const [tried, setTried] = useState(false);
  const [sw, setSw] = useState(true);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const toggle = (id: string) => setPills((list) => (list.includes(id) ? list.filter((x) => x !== id) : [...list, id]));

  return (
    <main className="min-h-dvh bg-bg px-[16px] py-[32px] text-text">
      <div className="mx-auto flex max-w-[1080px] flex-col gap-[20px]">
        <SectionHeader level="page" as="h1" title="Компоненты единой шкалы" meta={<span>UI_RULES.md · только dev</span>} />

        <Block title="Шкала кеглей">
          <div className="flex flex-col gap-[6px]">
            <span className="text-ui-32">32 · Ночной город</span>
            <span className="text-ui-24">24 · Все проекты</span>
            <span className="text-ui-20">20 · Продолжить</span>
            <span className="text-ui-16">16 · Основной текст и обычные кнопки</span>
            <span className="text-ui-14 text-text-60">14 · Вторичный текст, мета, пилюли</span>
            <span className="text-ui-12 text-text-40">12 · Подписи, метки, счётчики</span>
          </div>
        </Block>

        <Block title="Кнопки">
          <Row>
            <Button variant="secondary" size="lg" iconOnly aria-label="Назад"><Icon>{GLYPH.arrowLeft}</Icon></Button>
            <Button variant="primary" size="lg" className="min-w-[280px]" iconEnd={<Icon>{GLYPH.arrowRight}</Icon>}>Продолжить</Button>
            <Button variant="primary" size="lg" ready={false} className="min-w-[280px]" iconEnd={<Icon>{GLYPH.arrowRight}</Icon>}>Продолжить</Button>
          </Row>
          <Row>
            <Button variant="primary">Создать проект</Button>
            <Button variant="secondary" iconEnd={<Icon>{GLYPH.down}</Icon>}>Месяц</Button>
            <Button variant="ghost">Сбросить</Button>
            <Button variant="secondary" iconOnly aria-label="Скачать"><Icon>{GLYPH.download}</Icon></Button>
            <Button variant="primary" loading={busy} onClick={() => { setBusy(true); window.setTimeout(() => setBusy(false), 1500); }}>Опубликовать</Button>
            <Button variant="secondary" disabled>Недоступно</Button>
          </Row>
          <Row>
            <Button size="sm" icon={<Icon>{GLYPH.download}</Icon>}>Скачать все</Button>
            <Button size="sm" variant="ghost">Заменить</Button>
            <Button size="sm" variant="ghost" iconEnd={<Icon>{GLYPH.right}</Icon>}>Все проекты</Button>
          </Row>
        </Block>

        <Block title="Табы и переключатели">
          <Segmented
            ariaLabel="Этапы"
            semantics="tabs"
            fill
            value={stage}
            onChange={setStage}
            items={[
              { value: 'track', label: 'Трек', done: true },
              { value: 'bg', label: 'Фон' },
              { value: 'text', label: 'Текст', icon: <Icon src="/assets/figma/icon-note.svg" heavy ratio={0.75} /> },
              { value: 'fx', label: 'FX', icon: <Icon src="/assets/figma/icon-bolt.svg" heavy ratio={0.65} /> },
              { value: 'pool', label: 'Пул', disabled: true }
            ]}
          />
          <Row>
            <Segmented ariaLabel="Батчи" semantics="tabs" value={batch} onChange={setBatch} onAdd={() => {}} addLabel="Новый батч" items={[{ value: '1', label: 'Батч №1' }, { value: '2', label: 'Батч №2' }]} />
            <Segmented ariaLabel="Тип футажей" size="sm" value={type} onChange={setType} items={[{ value: 'vertical', label: 'Вертикальные 9:16' }, { value: 'cine', label: 'Кино 16:9' }, { value: 'films', label: 'Фильмы' }]} />
            <Segmented ariaLabel="Оценка" size="sm" value={rate} onChange={setRate} items={['1', '2', '3', '4', '5'].map((v) => ({ value: v, label: v }))} />
          </Row>
        </Block>

        <Block title="Пейджер, метки и пилюли">
          <Row>
            <Pager index={page} total={3} onPrev={() => setPage((p) => (p + 2) % 3)} onNext={() => setPage((p) => (p + 1) % 3)} />
            <span className="rounded-r15 bg-accent-strong p-[10px]"><Pager tone="overlay" index={0} total={2} onPrev={() => {}} onNext={() => {}} /></span>
          </Row>
          <Row>
            <Tag>1:35</Tag><Tag>MP3</Tag><Tag>Ночной город</Tag><Tag tone="accent">Jakson</Tag>
            <Tag tone="ok">Выложено</Tag><Tag tone="warn">Настроить</Tag><Tag tone="error">Ошибка</Tag>
          </Row>
          <Row>
            <Pill pressed={pills.includes('night')} onClick={() => toggle('night')}>#ночнойгород</Pill>
            <Pill pressed={pills.includes('snap')} onClick={() => toggle('snap')}>Щелчок</Pill>
            <Pill pressed={pills.includes('foot')} count={2} onClick={() => toggle('foot')}>Футажи</Pill>
            <Pill count={0} disabled>Цвет</Pill>
          </Row>
        </Block>

        <Block title="Панели, пустое место, строка действий">
          <Surface className="px-[16px] py-[14px] text-ui-14 text-text-60">night_city · 1:35 · MP3</Surface>
          <DropZone title="Перетащи трек или выбери файл" hint="MP3, WAV, M4A, OGG или FLAC" accept="audio/*" onFiles={() => setTried(false)} invalid={tried} />
          <Surface level="card" className="p-[16px]">
            <ActionBar
              start={<Checkbox checked={agree} onChange={setAgree} invalid={tried && !agree} label="Музыка и видео мои" hint="Подтверждается для каждого ролика" />}
            >
              <Button variant="secondary" size="lg" iconOnly aria-label="Назад"><Icon>{GLYPH.arrowLeft}</Icon></Button>
              <Button variant="primary" size="lg" ready={agree} className="flex-1 md:min-w-[240px]" iconEnd={<Icon>{GLYPH.arrowRight}</Icon>} onClick={() => setTried(!agree)}>Опубликовать</Button>
            </ActionBar>
          </Surface>
        </Block>

        <Block title="Поля, галочки, тумблеры, модалка">
          <Row top>
            <TextField label="Начало" placeholder="м:сс" defaultValue="0:40" className="w-[160px]" />
            <TextField label="Конец" placeholder="м:сс" defaultValue="0:15" error="Конец раньше начала" className="w-[200px]" />
            <TextField label="Описание" placeholder="О чём этот ролик?" hint="До 2200 символов" className="w-[260px]" />
          </Row>
          <Row>
            <Checkbox checked={agree} onChange={setAgree} label="Галочка с подписью" />
            <span className="inline-flex items-center gap-[10px] text-ui-14 text-text-80"><Switch checked={sw} onChange={setSw} label="Комментарии" />Комментарии</span>
            <Switch checked={false} onChange={() => {}} label="Выключен" disabled />
            <Button onClick={() => setOpen(true)}>Открыть модалку</Button>
          </Row>
        </Block>
      </div>

      <Dialog
        open={open}
        onClose={() => setOpen(false)}
        title="Свои исходники"
        description="Собери одно или несколько видео из своих клипов."
        footer={<ActionBar start={<span>В Пул уйдёт видео: 1</span>}><Button variant="primary" onClick={() => setOpen(false)}>Готово</Button></ActionBar>}
      >
        <DropZone title="Перетащи клипы или выбери MP4" hint="Видео 9:16, от 1 до 600 с" accept="video/*" multiple onFiles={() => {}} />
      </Dialog>
    </main>
  );
}
