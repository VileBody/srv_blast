/*
 * Общие компоненты единой шкалы UI (web_app/frontend/UI_RULES.md).
 * Новый код и переводимые экраны берут компоненты отсюда; старые Button/Input/Modal из
 * components/ui живут до зачистки (волна 5). Витрина всех состояний: /dev/kit (только dev).
 */
export { ActionBar } from './ActionBar';
export { Button, ButtonLink, buttonClass, type ButtonProps, type ButtonSize, type ButtonVariant } from './Button';
export { Dialog } from './Dialog';
export { Checkbox, Switch, TextField } from './Field';
export { GLYPH, Icon, type IconTone } from './Icon';
export { Pager } from './Pager';
export { DropZone, Surface } from './Panel';
export { SectionHeader } from './SectionHeader';
export { Segmented, type SegmentedItem } from './Segmented';
export { Pill, Tag, type TagTone } from './Tag';
