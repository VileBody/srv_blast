import { useEffect, useState } from 'react';
import type { Taxonomy } from '../types';
import { fetchTaxonomy } from '../api';

export function useTaxonomy() {
  const [taxonomy, setTaxonomy] = useState<Taxonomy | null>(null);

  useEffect(() => {
    fetchTaxonomy().then(setTaxonomy).catch(console.error);
  }, []);

  return taxonomy;
}
