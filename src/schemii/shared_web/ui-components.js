(() => {
  const ICONS = Object.freeze({
    close: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="m5 5 10 10M15 5 5 15"/></svg>',
    sql: '<svg viewBox="0 0 20 20" aria-hidden="true"><rect x="3" y="3.5" width="14" height="13" rx="2"/><path d="m6.5 8 2 2-2 2M10.5 12h3"/></svg>',
    database: '<svg viewBox="0 0 20 20" aria-hidden="true"><ellipse cx="10" cy="5" rx="6.5" ry="2.5"/><path d="M3.5 5v5c0 1.4 2.9 2.5 6.5 2.5s6.5-1.1 6.5-2.5V5M3.5 10v5c0 1.4 2.9 2.5 6.5 2.5s6.5-1.1 6.5-2.5v-5"/></svg>',
    edit: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="m4 14.5-.5 3 3-.5L16 7.5 12.5 4Z"/><path d="m11 5.5 3.5 3.5"/></svg>',
    earlier: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="m9 5-5 5 5 5M4 10h12"/></svg>',
    later: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="m11 5 5 5-5 5M4 10h12"/></svg>',
    duplicate: '<svg viewBox="0 0 20 20" aria-hidden="true"><rect x="7" y="7" width="9" height="9" rx="1.5"/><path d="M13 7V5.5A1.5 1.5 0 0 0 11.5 4h-7A1.5 1.5 0 0 0 3 5.5v7A1.5 1.5 0 0 0 4.5 14H7"/></svg>',
    delete: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M4 6h12M8 3h4l1 3H7l1-3ZM6 6l1 11h6l1-11M9 9v5M11 9v5"/></svg>',
    add: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M10 4v12M4 10h12"/></svg>',
    refresh: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M15.5 7A6 6 0 1 0 16 12"/><path d="M15.5 3.5V7H12"/></svg>',
    more: '<svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="5" cy="10" r="1"/><circle cx="10" cy="10" r="1"/><circle cx="15" cy="10" r="1"/></svg>',
  });

  function decorateIconControl(control, { icon, label, tooltip = label, placement = "top", id = "", className = "", dataset = {}, attributes = {} }) {
    if (!ICONS[icon] || typeof label !== "string" || !label) throw new TypeError("A known icon and accessible label are required");
    control.className = `shared-icon-button${className ? ` ${className}` : ""}`;
    if (id) control.id = id;
    control.setAttribute("aria-label", label);
    if (tooltip) control.dataset.tooltip = tooltip;
    if (placement) control.dataset.tooltipPlacement = placement;
    for (const [name, value] of Object.entries(dataset)) control.dataset[name] = String(value);
    for (const [name, value] of Object.entries(attributes)) control.setAttribute(name, String(value));
    control.innerHTML = ICONS[icon];
    return control;
  }

  function createIconButton(options) {
    const button = document.createElement("button");
    button.type = "button";
    return decorateIconControl(button, options);
  }

  function createTooltipController({ element }) {
    if (!(element instanceof HTMLElement)) throw new TypeError("A tooltip element is required");
    let activeTarget = null;
    let hideTimer = null;

    function position(target) {
      const targetRect = target.getBoundingClientRect();
      const tooltipRect = element.getBoundingClientRect();
      const gap = 9;
      const margin = 8;
      let placement = target.dataset.tooltipPlacement || (target.closest(".tool-rail") ? "right" : "top");
      if (placement === "right" && targetRect.right + gap + tooltipRect.width > window.innerWidth - margin) placement = "left";
      if (placement === "left" && targetRect.left - gap - tooltipRect.width < margin) placement = "right";
      if (placement === "top" && targetRect.top - gap - tooltipRect.height < margin) placement = "bottom";
      if (placement === "bottom" && targetRect.bottom + gap + tooltipRect.height > window.innerHeight - margin) placement = "top";
      let left;
      let top;
      if (placement === "right" || placement === "left") {
        left = placement === "right" ? targetRect.right + gap : targetRect.left - tooltipRect.width - gap;
        top = targetRect.top + (targetRect.height - tooltipRect.height) / 2;
      } else {
        left = targetRect.left + (targetRect.width - tooltipRect.width) / 2;
        top = placement === "bottom" ? targetRect.bottom + gap : targetRect.top - tooltipRect.height - gap;
      }
      element.dataset.placement = placement;
      element.style.left = `${Math.max(margin, Math.min(left, window.innerWidth - tooltipRect.width - margin))}px`;
      element.style.top = `${Math.max(margin, Math.min(top, window.innerHeight - tooltipRect.height - margin))}px`;
    }

    function show(target) {
      const nativeTitle = target.getAttribute("title");
      if (nativeTitle) {
        target.dataset.tooltip = nativeTitle;
        target.removeAttribute("title");
      }
      if (!target.dataset.tooltip) return;
      clearTimeout(hideTimer);
      activeTarget = target;
      element.textContent = target.dataset.tooltip;
      element.classList.remove("visible");
      element.hidden = false;
      position(target);
      requestAnimationFrame(() => {
        if (activeTarget === target) element.classList.add("visible");
      });
    }

    function hide() {
      activeTarget = null;
      element.classList.remove("visible");
      clearTimeout(hideTimer);
      hideTimer = setTimeout(() => { element.hidden = true; }, 150);
    }

    function update(target, text) {
      target.dataset.tooltip = text;
      delete target.dataset.tooltipAutomatic;
      if (activeTarget !== target) return;
      element.textContent = text;
      position(target);
    }

    return Object.freeze({ show, hide, update, get activeTarget() { return activeTarget; } });
  }

  window.SchemiiShared = Object.freeze({ ...window.SchemiiShared, ICONS, decorateIconControl, createIconButton, createTooltipController });
})();
