type TagState = 'unassigned' | 'included' | 'excluded';

interface Props {
  tag: string;
  state: TagState;
  onClick: (tag: string) => void;
}

const STATE_CLASSES: Record<TagState, string> = {
  unassigned: 'chip-unassigned',
  included: 'chip-included',
  excluded: 'chip-excluded',
};

const STATE_LABELS: Record<TagState, string> = {
  unassigned: '',
  included: '+',
  excluded: '−',
};

export function TagChip({ tag, state, onClick }: Props) {
  return (
    <button
      className={`tag-chip ${STATE_CLASSES[state]}`}
      onClick={() => onClick(tag)}
      title={state}
    >
      {STATE_LABELS[state] ? `${STATE_LABELS[state]} ` : ''}{tag}
    </button>
  );
}
