interface Props {
  index: number;
  total: number;
  onPrev: () => void;
  onNext: () => void;
  onDelete: () => void;
}

export function NavigationBar({ index, total, onPrev, onNext, onDelete }: Props) {
  return (
    <div className="nav-bar">
      <button onClick={onPrev} disabled={index <= 0}>← Назад</button>
      <span className="nav-counter">{total > 0 ? `${index + 1} из ${total}` : '—'}</span>
      <button onClick={onNext} disabled={index >= total - 1}>Вперёд →</button>
      <button className="btn-delete" onClick={onDelete} disabled={total <= 0}>Удалить</button>
    </div>
  );
}
