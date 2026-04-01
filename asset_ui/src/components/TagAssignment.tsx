import { useCallback, useEffect, useState } from 'react';
import type { Asset, Taxonomy, ThemeAssignment } from '../types';
import { updateTags } from '../api';
import { TagChip } from './TagChip';

interface Props {
  asset: Asset | null;
  taxonomy: Taxonomy | null;
  onSaved: () => void;
}

type TagState = 'unassigned' | 'included' | 'excluded';

export function TagAssignment({ asset, taxonomy, onSaved }: Props) {
  const [open, setOpen] = useState(false);
  const [selectedTheme, setSelectedTheme] = useState<string | null>(null);
  const [selectedGroup, setSelectedGroup] = useState<string | null>(null);
  const [tagStates, setTagStates] = useState<Record<string, TagState>>({});
  const [saving, setSaving] = useState(false);

  // Reset on asset change
  useEffect(() => {
    setOpen(false);
    setSelectedTheme(null);
    setSelectedGroup(null);
    setTagStates({});
  }, [asset?.file_name, asset?.s3_key]);

  // Load existing overrides when theme/group selected
  useEffect(() => {
    if (!asset || !selectedTheme || !selectedGroup) return;
    const existing = asset.overrides?.theme_assignments?.find(
      (a) => a.theme === selectedTheme && a.group === selectedGroup,
    );
    if (existing) {
      const states: Record<string, TagState> = {};
      existing.tags.forEach((t) => { states[t] = 'included'; });
      existing.excluded_tags.forEach((t) => { states[t] = 'excluded'; });
      setTagStates(states);
    } else {
      setTagStates({});
    }
  }, [asset, selectedTheme, selectedGroup]);

  const cycleTag = useCallback((tag: string) => {
    setTagStates((prev) => {
      const current = prev[tag] || 'unassigned';
      const next: TagState =
        current === 'unassigned' ? 'included' :
        current === 'included' ? 'excluded' : 'unassigned';
      if (next === 'unassigned') {
        const { [tag]: _, ...rest } = prev;
        return rest;
      }
      return { ...prev, [tag]: next };
    });
  }, []);

  const save = useCallback(async () => {
    if (!asset || !selectedTheme || !selectedGroup) return;
    setSaving(true);
    try {
      const tags = Object.entries(tagStates).filter(([, s]) => s === 'included').map(([t]) => t);
      const excludedTags = Object.entries(tagStates).filter(([, s]) => s === 'excluded').map(([t]) => t);

      // Merge with existing assignments for other theme/groups
      const existing = (asset.overrides?.theme_assignments ?? [])
        .filter((a) => !(a.theme === selectedTheme && a.group === selectedGroup));

      const newAssignment: ThemeAssignment = {
        theme: selectedTheme,
        group: selectedGroup,
        tags,
        excluded_tags: excludedTags,
      };

      await updateTags(asset.file_name, [...existing, newAssignment], asset.s3_key);
      onSaved();
    } catch (e) {
      console.error('Failed to save tags', e);
    } finally {
      setSaving(false);
    }
  }, [asset, selectedTheme, selectedGroup, tagStates, onSaved]);

  if (!asset || !taxonomy) return null;

  const themeNames = Object.keys(taxonomy);
  const themeData = selectedTheme ? taxonomy[selectedTheme] : null;
  const groupNames = themeData ? Object.keys(themeData.tags_groups) : [];
  const groupData = themeData && selectedGroup ? themeData.tags_groups[selectedGroup] : null;

  return (
    <div className="tag-assignment">
      <button className="btn-tags" onClick={() => setOpen(!open)}>
        {open ? 'Скрыть теги' : 'Теги видео'}
      </button>

      {/* Existing assignments */}
      {asset.overrides?.theme_assignments && asset.overrides.theme_assignments.length > 0 && (
        <div className="existing-tags">
          {asset.overrides.theme_assignments.map((a, i) => (
            <div key={i} className="assignment-badge">
              <strong>{a.theme}</strong> / {a.group}
              {a.tags.length > 0 && <span className="badge-tags"> +{a.tags.length}</span>}
              {a.excluded_tags.length > 0 && <span className="badge-excluded"> −{a.excluded_tags.length}</span>}
            </div>
          ))}
        </div>
      )}

      {open && (
        <div className="tag-panel">
          {/* Theme selector */}
          <div className="theme-list">
            <h4>Тема</h4>
            {themeNames.map((name) => (
              <button
                key={name}
                className={`theme-btn ${selectedTheme === name ? 'active' : ''}`}
                onClick={() => { setSelectedTheme(name); setSelectedGroup(null); setTagStates({}); }}
              >
                {name.replace('_', ' ')}
                <span className="theme-mood">{name.endsWith('_major') ? 'major' : name.endsWith('_minor') ? 'minor' : ''}</span>
              </button>
            ))}
          </div>

          {/* Group selector */}
          {themeData && (
            <div className="group-list">
              <h4>Группа</h4>
              <div className="theme-meta">
                <span>Цвет: {themeData.color.join(', ')}</span>
                <span>Исключить: {themeData.exclude.join(', ')}</span>
              </div>
              {groupNames.map((gn) => {
                const gd = themeData.tags_groups[gn];
                return (
                  <button
                    key={gn}
                    className={`group-btn ${selectedGroup === gn ? 'active' : ''}`}
                    onClick={() => { setSelectedGroup(gn); setTagStates({}); }}
                  >
                    {gn.replace(/_/g, ' ')}
                    {gd._people && <span className="group-people">{gd._people}</span>}
                    <span className="group-count">{gd._tags.length} тегов</span>
                  </button>
                );
              })}
            </div>
          )}

          {/* Tag chips */}
          {groupData && (
            <div className="tag-chips-panel">
              <h4>Теги ({groupData._tags.length})</h4>
              {groupData._exclude_tags && groupData._exclude_tags.length > 0 && (
                <div className="exclude-warning">
                  Авто-исключение: {groupData._exclude_tags.join(', ')}
                </div>
              )}
              <div className="tag-chips">
                {groupData._tags.map((t) => (
                  <TagChip
                    key={t}
                    tag={t}
                    state={tagStates[t] || 'unassigned'}
                    onClick={cycleTag}
                  />
                ))}
              </div>
              <button className="btn-save" onClick={save} disabled={saving}>
                {saving ? 'Сохранение...' : 'Сохранить'}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
