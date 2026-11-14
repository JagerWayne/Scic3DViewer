// Backdrop gradients, shared by the 3D stage and the lightweight image viewer (which must not load Three.js).
export const BACKDROPS = {
  midnight: 'radial-gradient(circle at 50% 30%,#1e293b,#020617)',
  slate: 'linear-gradient(#334155,#0f172a)',
  studio: 'linear-gradient(#9ca3af,#4b5563)',
  sunset: 'linear-gradient(#7c3aed,#f97316)',
  white: '#f8fafc',
  transparent: 'repeating-conic-gradient(#64748b 0% 25%,#475569 0% 50%) 50%/24px 24px',
};
// Solid stand-in colours for browsers that cannot encode transparent WebP (frames are then flat JPEGs)
export const BACKDROP_FLAT = { midnight: '#0f172a', slate: '#1e293b', studio: '#6b7280', sunset: '#a855f7', white: '#f8fafc', transparent: '#475569' };
