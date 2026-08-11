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
    search: '<svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="8.5" cy="8.5" r="5"/><path d="m12.2 12.2 4.3 4.3"/></svg>',
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

  function elementHasTruncatedText(element) {
    if (!element || element.hidden) return false;
    const style = getComputedStyle(element);
    const lineClamp = Number.parseInt(style.webkitLineClamp, 10);
    const truncates = style.textOverflow === "ellipsis" || (Number.isFinite(lineClamp) && lineClamp > 0);
    return truncates && (element.scrollWidth > element.clientWidth + 1 || element.scrollHeight > element.clientHeight + 1);
  }

  function automaticTooltipText(element) {
    const value = typeof element?.value === "string" ? element.value : element?.textContent;
    return String(value ?? "").replace(/\s+/g, " ").trim();
  }

  function findTooltipTarget(start, { includeDescendants = false, automaticTruncation = false, boundary = document.body } = {}) {
    for (let target = start; target && target !== boundary; target = target.parentElement) {
      const automatic = target.dataset?.tooltipAutomatic === "true";
      if (!automatic && (target.dataset?.tooltip || target.getAttribute?.("title"))) return target;
      const truncated = automaticTruncation && elementHasTruncatedText(target);
      if (automatic) {
        if (!truncated) {
          delete target.dataset.tooltip;
          delete target.dataset.tooltipAutomatic;
        } else {
          target.dataset.tooltip = automaticTooltipText(target);
        }
      }
      if (target.dataset?.tooltip || target.getAttribute?.("title")) return target;
      if (truncated) {
        const text = automaticTooltipText(target);
        if (text) {
          target.dataset.tooltip = text;
          target.dataset.tooltipAutomatic = "true";
          return target;
        }
      }
    }
    if (includeDescendants) {
      for (const target of start?.querySelectorAll?.("*") ?? []) {
        const match = findTooltipTarget(target, { automaticTruncation, boundary });
        if (match) return match;
      }
    }
    return null;
  }

  function installTooltipDelegation({ controller, root = document, resolveTarget = target => findTooltipTarget(target), hideOnClick = false, onScroll = null } = {}) {
    if (!controller) throw new TypeError("A tooltip controller is required");
    const listeners = [];
    const listen = (type, callback, options) => {
      root.addEventListener(type, callback, options);
      listeners.push([type, callback, options]);
    };
    listen("pointerover", event => {
      const target = resolveTarget(event.target, false);
      if (target && target !== controller.activeTarget) controller.show(target);
    });
    listen("pointerout", event => {
      if (!controller.activeTarget || controller.activeTarget.contains(event.relatedTarget)) return;
      controller.hide();
    });
    listen("focusin", event => {
      const target = resolveTarget(event.target, true);
      if (target) controller.show(target);
    });
    listen("focusout", event => {
      if (!controller.activeTarget || controller.activeTarget.contains(event.relatedTarget)) return;
      controller.hide();
    });
    listen("pointerdown", () => controller.hide());
    if (hideOnClick) listen("click", () => controller.hide());
    listen("scroll", () => {
      controller.hide();
      if (typeof onScroll === "function") onScroll();
    }, true);
    return Object.freeze({
      destroy() {
        for (const [type, callback, options] of listeners) root.removeEventListener(type, callback, options);
      }
    });
  }

  function setControlStatus(element, message, { state = "info", hideWhenEmpty = false } = {}) {
    element.textContent = message;
    element.dataset.state = state;
    element.classList.toggle("error", state === "error");
    if (hideWhenEmpty) element.hidden = !message;
  }

  const loadingStates = new WeakMap();

  function setControlLoading(control, loading, { label = null, loadingLabel = "Working...", disable = true } = {}) {
    if (loading) {
      if (!loadingStates.has(control)) loadingStates.set(control, {
        disabled: control.disabled,
        ariaLabel: control.getAttribute("aria-label"),
        tooltip: control.dataset.tooltip,
      });
      control.setAttribute("aria-busy", "true");
      control.classList.add("shared-control-loading");
      if (disable) control.disabled = true;
      if (loadingLabel) {
        control.setAttribute("aria-label", loadingLabel);
        control.dataset.tooltip = loadingLabel;
      }
      return;
    }
    const state = loadingStates.get(control);
    control.removeAttribute("aria-busy");
    control.classList.remove("shared-control-loading");
    if (!state) return;
    control.disabled = state.disabled;
    const restoredLabel = label ?? state.ariaLabel;
    if (restoredLabel) control.setAttribute("aria-label", restoredLabel); else control.removeAttribute("aria-label");
    const restoredTooltip = label ?? state.tooltip;
    if (restoredTooltip) control.dataset.tooltip = restoredTooltip; else delete control.dataset.tooltip;
    loadingStates.delete(control);
  }

  async function withLoadingControl(control, options, operation) {
    setControlLoading(control, true, options);
    try {
      return await operation();
    } finally {
      setControlLoading(control, false, options);
    }
  }

  function installDetailsMenu(menu, { closeOnAction = true, closeOnOutside = true, closeOnEscape = true } = {}) {
    const onClick = event => {
      if (closeOnAction && event.target.closest?.("button, a, [role='menuitem']")) menu.removeAttribute("open");
    };
    const onDocumentClick = event => {
      if (closeOnOutside && menu.open && !menu.contains(event.target)) menu.removeAttribute("open");
    };
    const onKeydown = event => {
      if (closeOnEscape && event.key === "Escape" && menu.open) {
        menu.removeAttribute("open");
        menu.querySelector("summary")?.focus();
      }
    };
    menu.addEventListener("click", onClick);
    document.addEventListener("click", onDocumentClick);
    document.addEventListener("keydown", onKeydown);
    return Object.freeze({ destroy() {
      menu.removeEventListener("click", onClick);
      document.removeEventListener("click", onDocumentClick);
      document.removeEventListener("keydown", onKeydown);
    } });
  }

  window.SchemiiShared = Object.freeze({
    ...(window.SchemiiShared || {}),
    ICONS, decorateIconControl, createIconButton, createTooltipController,
    elementHasTruncatedText, automaticTooltipText, findTooltipTarget, installTooltipDelegation,
    setControlStatus, setControlLoading, withLoadingControl, installDetailsMenu,
  });
})();
