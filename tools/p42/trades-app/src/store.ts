import {useEffect, useState, useSyncExternalStore} from 'react';

// Общее состояние со страницей: объект TR (период, кластер, открытая сделка) живёт на странице, хеш пишет её setHash().
export const shared: {state: {per: string; cl: number; open: number | null}; onChange: () => void} = {state: {per: 'main', cl: 0, open: null}, onChange: () => {}};
let ver = 0;
const subs = new Set<() => void>();
export const bump = () => { ver++; subs.forEach(f => f()); };
export const useStore = () => useSyncExternalStore(cb => { subs.add(cb); return () => subs.delete(cb); }, () => ver);
export function update(patch: Partial<typeof shared.state>) { Object.assign(shared.state, patch); shared.onChange(); bump(); }

// Тема — класс dark на <html> (страница переключает; здесь только читаем).
export function useDark() {
  const get = () => document.documentElement.classList.contains('dark');
  const [dark, set] = useState(get());
  useEffect(() => {
    const mo = new MutationObserver(() => set(get()));
    mo.observe(document.documentElement, {attributes: true, attributeFilter: ['class']});
    return () => mo.disconnect();
  }, []);
  return dark;
}
