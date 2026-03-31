import { useCallback, useEffect, useRef, useState } from 'react';
import type { TagStatus, Taxonomy } from '../types';
import { blacklistTag, unblacklistTag, assignTag, unassignTag } from '../api';

interface Props {
  tags: string[];
  tagStatuses: Record<string, TagStatus>;
  taxonomy: Taxonomy | null;
  onSaved: () => void;
}

export function ThemeTagPills({ tags, tagStatuses, taxonomy, onSaved }: Props) {
  const [activeTag, setActiveTag] = useState<string | null>(null);
  const [assignStep, setAssignStep] = useState<'pick-theme' | 'pick-group' | null>(null);
  const [selectedTheme, setSelectedTheme] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const popoverRef = useRef<HTMLDivElement>(null);

  // Close popover on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (popoverRef.current && !popoverRef.current.contains(e.target as Node)) {
        setActiveTag(null);
        setAssignStep(null);
        setSelectedTheme(null);
      }
    };
    if (activeTag) {
      document.addEventListener('mousedown', handler);
      return () => document.removeEventListener('mousedown', handler);
    }
  }, [activeTag]);

  // Reset popover on tag change
  useEffect(() => {
    setActiveTag(null);
    setAssignStep(null);
    setSelectedTheme(null);
  }, [tags]);

  const handlePillClick = useCallback((tag: string) => {
    if (activeTag === tag) {
      setActiveTag(null);
      setAssignStep(null);
      setSelectedTheme(null);
    } else {
      setActiveTag(tag);
      setAssignStep(null);
      setSelectedTheme(null);
    }
  }, [activeTag]);

  const handleBlacklist = useCallback(async (tag: string) => {
    setSaving(true);
    try {
      await blacklistTag(tag);
      setActiveTag(null);
      onSaved();
    } catch (e) {
      console.error('Failed to blacklist tag', e);
    } finally {
      setSaving(false);
    }
  }, [onSaved]);

  const handleUnblacklist = useCallback(async (tag: string) => {
    setSaving(true);
    try {
      await unblacklistTag(tag);
      setActiveTag(null);
      onSaved();
    } catch (e) {
      console.error('Failed to unblacklist tag', e);
    } finally {
      setSaving(false);
    }
  }, [onSaved]);

  const handleAssign = useCallback(async (tag: string, theme: string, group: string) => {
    setSaving(true);
    try {
      await assignTag(tag, theme, group);
      setActiveTag(null);
      setAssignStep(null);
      setSelectedTheme(null);
      onSaved();
    } catch (e) {
      console.error('Failed to assign tag', e);
    } finally {
      setSaving(false);
    }
  }, [onSaved]);

  const handleUnassign = useCallback(async (tag: string, theme: string, group: string) => {
    setSaving(true);
    try {
      await unassignTag(tag, theme, group);
      onSaved();
    } catch (e) {
      console.error('Failed to unassign tag', e);
    } finally {
      setSaving(false);
    }
  }, [onSaved]);

  if (!tags || tags.length === 0) {
    return <div className="theme-tag-pills-empty">Нет тегов</div>;
  }

  const themeNames = taxonomy ? Object.keys(taxonomy) : [];
  const groupNames = taxonomy && selectedTheme && taxonomy[selectedTheme]
    ? Object.keys(taxonomy[selectedTheme].tags_groups)
    : [];

  return (
    <div className="theme-tag-pills-section">
      <h4 className="pills-header">Теги видео ({tags.length})</h4>
      <div className="theme-tag-pills">
        {tags.map((tag) => {
          const status = tagStatuses[tag];
          const isBlacklisted = status?.blacklisted;
          const isAssigned = status?.assigned_to && status.assigned_to.length > 0;

          let pillClass = 'pill-neutral';
          if (isBlacklisted) pillClass = 'pill-blacklisted';
          else if (isAssigned) pillClass = 'pill-assigned';

          return (
            <div key={tag} className="pill-wrapper">
              <button
                className={`theme-tag-pill ${pillClass} ${activeTag === tag ? 'pill-active' : ''}`}
                onClick={() => handlePillClick(tag)}
                disabled={saving}
              >
                {isBlacklisted && <span className="pill-icon">−</span>}
                {isAssigned && !isBlacklisted && <span className="pill-icon">+</span>}
                {tag}
              </button>

              {activeTag === tag && (
                <div className="pill-popover" ref={popoverRef}>
                  <div className="popover-tag-name">{tag}</div>

                  {/* Current assignments */}
                  {isAssigned && (
                    <div className="popover-assignments">
                      {status!.assigned_to!.map((a, i) => (
                        <div key={i} className="popover-assignment-row">
                          <span className="assignment-label">
                            {a.theme} / {a.group}
                          </span>
                          <button
                            className="btn-remove-assign"
                            onClick={() => handleUnassign(tag, a.theme, a.group)}
                            disabled={saving}
                          >
                            ×
                          </button>
                        </div>
                      ))}
                    </div>
                  )}

                  {/* Action buttons */}
                  {!assignStep && (
                    <div className="popover-actions">
                      {isBlacklisted ? (
                        <button
                          className="btn-popover btn-restore"
                          onClick={() => handleUnblacklist(tag)}
                          disabled={saving}
                        >
                          Восстановить
                        </button>
                      ) : (
                        <>
                          <button
                            className="btn-popover btn-add-theme"
                            onClick={() => setAssignStep('pick-theme')}
                            disabled={saving}
                          >
                            Добавить в тему
                          </button>
                          <button
                            className="btn-popover btn-blacklist"
                            onClick={() => handleBlacklist(tag)}
                            disabled={saving}
                          >
                            Удалить
                          </button>
                        </>
                      )}
                    </div>
                  )}

                  {/* Theme picker */}
                  {assignStep === 'pick-theme' && (
                    <div className="popover-picker">
                      <div className="picker-header">
                        <button className="btn-picker-back" onClick={() => setAssignStep(null)}>←</button>
                        <span>Выберите тему</span>
                      </div>
                      <div className="picker-list">
                        {themeNames.map((name) => (
                          <button
                            key={name}
                            className="picker-item"
                            onClick={() => { setSelectedTheme(name); setAssignStep('pick-group'); }}
                          >
                            {name.replace(/_/g, ' ')}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Group picker */}
                  {assignStep === 'pick-group' && selectedTheme && (
                    <div className="popover-picker">
                      <div className="picker-header">
                        <button className="btn-picker-back" onClick={() => { setAssignStep('pick-theme'); setSelectedTheme(null); }}>←</button>
                        <span>{selectedTheme.replace(/_/g, ' ')}</span>
                      </div>
                      <div className="picker-list">
                        {groupNames.map((gn) => (
                          <button
                            key={gn}
                            className="picker-item"
                            onClick={() => handleAssign(tag, selectedTheme, gn)}
                            disabled={saving}
                          >
                            {gn.replace(/_/g, ' ')}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
