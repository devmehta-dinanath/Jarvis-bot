// Deterministic per-name gradient so avatars are visually distinct instead of one
// flat color for every contact. Same name always maps to the same pair.
const GRADIENT_PAIRS = [
  ["#5b93ff", "#2fd6ac"],
  ["#ff9166", "#ef5f75"],
  ["#f0b84f", "#ff9166"],
  ["#2fd6ac", "#5b93ff"],
  ["#a78bfa", "#5b93ff"],
  ["#ef5f75", "#f0b84f"],
  ["#2fd6ac", "#a78bfa"],
  ["#5b93ff", "#a78bfa"]
];

function hashString(value) {
  let hash = 0;
  for (let i = 0; i < value.length; i += 1) {
    hash = (hash * 31 + value.charCodeAt(i)) | 0;
  }
  return Math.abs(hash);
}

/** CSS custom properties to spread onto an element's inline style for `.conversation-item__avatar`. */
export function avatarGradient(name) {
  const key = (name || "").trim().toLowerCase() || "?";
  const [from, to] = GRADIENT_PAIRS[hashString(key) % GRADIENT_PAIRS.length];
  return { "--avatar-from": from, "--avatar-to": to };
}

export function applyAvatarGradient(element, name) {
  const vars = avatarGradient(name);
  for (const [prop, value] of Object.entries(vars)) {
    element.style.setProperty(prop, value);
  }
}
