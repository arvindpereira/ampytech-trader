/** Backend API origin for browser fetches.
 *
 * When you open the dashboard from another device on your LAN (e.g. http://10.0.0.43:3002),
 * API calls must target the same host — not localhost (which would mean the phone itself).
 *
 * Override with NEXT_PUBLIC_API_BASE in frontend/.env.local if needed.
 */
export function apiBase(): string {
  if (process.env.NEXT_PUBLIC_API_BASE) {
    return process.env.NEXT_PUBLIC_API_BASE.replace(/\/$/, '');
  }
  if (typeof window !== 'undefined') {
    return `http://${window.location.hostname}:8008`;
  }
  return 'http://localhost:8008';
}

export function apiUrl(path: string): string {
  const p = path.startsWith('/') ? path : `/${path}`;
  return `${apiBase()}${p}`;
}
