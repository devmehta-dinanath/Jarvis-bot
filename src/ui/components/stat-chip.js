export function createStatChip(value, label, variant = "default") {
  const chip = document.createElement("div");
  chip.className = `stat-chip stat-chip--${variant}`;

  const valueEl = document.createElement("span");
  valueEl.className = "stat-chip__value";
  valueEl.textContent = String(value);

  const labelEl = document.createElement("span");
  labelEl.className = "stat-chip__label";
  labelEl.textContent = label;

  chip.append(valueEl, labelEl);
  return chip;
}
