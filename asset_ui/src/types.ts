export interface Asset {
  file_name: string;
  genre: string;
  tag: string;
  src_w: number;
  src_h: number;
  duration_sec: number;
  dominant_color?: string;
  palette_bins?: { bin: string; weight: number }[];
  overrides?: AssetOverride;
}

export interface AssetOverride {
  excluded?: boolean;
  theme_assignments?: ThemeAssignment[];
}

export interface ThemeAssignment {
  theme: string;
  group: string;
  tags: string[];
  excluded_tags: string[];
}

export interface PaginatedAssets {
  total: number;
  page: number;
  per_page: number;
  items: Asset[];
}

export interface TagGroup {
  _tags: string[];
  _exclude_tags?: string[];
  _color?: string[];
  _people?: string;
}

export interface ThemeData {
  color: string[];
  exclude: string[];
  tags_groups: Record<string, TagGroup>;
}

export type Taxonomy = Record<string, ThemeData>;
