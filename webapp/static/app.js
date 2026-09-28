(() => {
  const tg = window.Telegram ? window.Telegram.WebApp : null;
  const $ = (sel) => document.querySelector(sel);
  const state = { content: null, current: "home" };

  if (tg) {
    tg.ready();
    tg.expand();
    try {
      tg.setHeaderColor("#0a0a0c");
      tg.setBackgroundColor("#0a0a0c");
    } catch (_) { /* старые версии Telegram */ }
    tg.BackButton.onClick(() => go("home"));
  }

  const haptic = (type) => {
    try { tg && tg.HapticFeedback.impactOccurred(type || "light"); } catch (_) {}
  };

  // ---------- навигация ----------

  function go(screen) {
    if (screen === state.current) return;
    document.querySelectorAll(".screen").forEach((el) => el.classList.toggle("active", el.id === screen));
    document.querySelectorAll(".pill").forEach((el) => el.classList.toggle("active", el.dataset.go === screen));
    state.current = screen;
    window.scrollTo(0, 0);
    if (tg) screen === "home" ? tg.BackButton.hide() : tg.BackButton.show();
    haptic();
  }

  document.addEventListener("click", (e) => {
    const target = e.target.closest("[data-go]");
    if (target) {
      e.preventDefault();
      go(target.dataset.go);
    }
  });

  // ---------- контент ----------

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  function render(c) {
    const promos = $("#promos");
    promos.replaceChildren();
    c.promos.forEach((raw) => {
      const [title, ...rest] = raw.split("\n");
      const card = el("div", "promo");
      card.append(el("p", "promo-title", title));
      if (rest.join("\n").trim()) card.append(el("p", "promo-text", rest.join("\n").trim()));
      promos.append(card);
    });
    $("#promos-block").hidden = c.promos.length === 0;

    const gallery = $("#gallery");
    gallery.replaceChildren();
    c.portfolio.forEach((src) => {
      const img = el("img");
      img.src = src;
      img.loading = "lazy";
      img.alt = "Работа мастера";
      img.addEventListener("click", () => openLightbox(src));
      gallery.append(img);
    });
    $("#gallery-empty").hidden = c.portfolio.length > 0;

    $("#master").textContent = c.master;
    $("#about-text").textContent = c.about;
    $("#address").textContent = c.address;
    $("#contacts").textContent = c.contacts;
    $("#contacts-card").hidden = !c.contacts;

    const faq = $("#faq-list");
    faq.replaceChildren();
    c.faq.forEach((item) => {
      const d = el("details");
      d.append(el("summary", null, item.q), el("p", null, item.a));
      faq.append(d);
    });
  }

  function openLink(url) {
    if (tg) tg.openLink(url);
    else window.open(url, "_blank");
  }

  $("#map-btn").addEventListener("click", () => {
    const address = (state.content && state.content.address) || "";
    openLink("https://yandex.ru/maps/?text=" + encodeURIComponent(address));
  });

  const lightbox = $("#lightbox");
  function openLightbox(src) {
    lightbox.querySelector("img").src = src;
    lightbox.hidden = false;
  }
  lightbox.addEventListener("click", () => { lightbox.hidden = true; });

  fetch("/api/content")
    .then((r) => r.json())
    .then((c) => { state.content = c; render(c); })
    .catch(() => {});

  // ---------- форма ----------

  const form = $("#apply-form");
  const errorBox = $("#form-error");
  const submitBtn = $("#submit-btn");

  const tgUser = tg && tg.initDataUnsafe && tg.initDataUnsafe.user;
  if (tgUser) form.name.value = [tgUser.first_name, tgUser.last_name].filter(Boolean).join(" ");

  function showError(text) {
    errorBox.textContent = text;
    errorBox.hidden = !text;
    if (text && tg) tg.HapticFeedback.notificationOccurred("error");
  }

  // Разрешение боту писать клиенту — нужно для подтверждения записи и напоминаний
  function askWriteAccess() {
    return new Promise((resolve) => {
      if (!tg || !tg.requestWriteAccess) return resolve();
      try { tg.requestWriteAccess(() => resolve()); } catch (_) { resolve(); }
    });
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    showError("");
    const payload = {
      initData: tg ? tg.initData : "",
      name: form.name.value.trim(),
      phone: form.phone.value.trim(),
      idea: form.idea.value.trim(),
    };
    if (!payload.name) return showError("Укажите, как к вам обращаться");
    if (payload.phone.replace(/\D/g, "").length < 10) return showError("Проверьте номер телефона");
    if (!payload.initData) return showError("Откройте приложение через Telegram, чтобы отправить заявку");

    submitBtn.disabled = true;
    submitBtn.textContent = "Отправляем…";
    try {
      await askWriteAccess();
      const res = await fetch("/api/apply", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || "Не удалось отправить заявку");
      if (tg) tg.HapticFeedback.notificationOccurred("success");
      const master = (state.content && state.content.master) || "Мастер";
      $("#success-text").textContent = `${master} свяжется с вами в ближайшее время.`;
      form.idea.value = "";
      go("success");
    } catch (err) {
      showError(err.message || "Нет соединения. Попробуйте ещё раз");
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = "Отправить заявку";
    }
  });
})();
