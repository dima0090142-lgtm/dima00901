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
    tg.BackButton.onClick(() => {
      if (!lightbox.hidden) closeLightbox(); else go("home");
    });
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

  // Буквы заголовка — отдельными span, чтобы они появлялись по очереди
  let letterIndex = 0;
  document.querySelectorAll(".hero-title .word").forEach((word) => {
    const text = word.textContent.trim();
    word.textContent = "";
    [...text].forEach((ch) => {
      const span = el("span", "ch", ch);
      span.style.setProperty("--i", letterIndex++);
      word.append(span);
    });
  });

  // Плавное появление блоков, когда они попадают на экран
  const revealer = "IntersectionObserver" in window
    ? new IntersectionObserver((entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add("in");
            revealer.unobserve(entry.target);
          }
        });
      }, { threshold: 0.12 })
    : null;
  function observeReveals(root) {
    root.querySelectorAll(".reveal:not(.in)").forEach((node, i) => {
      node.style.transitionDelay = `${Math.min(i, 6) * 70}ms`;
      if (revealer) revealer.observe(node); else node.classList.add("in");
    });
  }

  function loadImg(img, src) {
    img.addEventListener("load", () => img.classList.add("loaded"), { once: true });
    img.src = src;
  }

  function render(c) {
    if (c.hero_photo) {
      const bg = $("#hero-bg");
      const probe = new Image();
      probe.onload = () => {
        bg.style.backgroundImage = `url("${c.hero_photo}")`;
        bg.classList.add("loaded");
      };
      probe.src = c.hero_photo;
    }

    const recent = $("#recent");
    recent.replaceChildren();
    c.portfolio.forEach((src, i) => {
      const img = el("img");
      img.loading = "lazy";
      img.alt = "Работа мастера";
      img.src = src;
      img.addEventListener("click", () => openLightbox(i));
      recent.append(img);
    });
    $("#recent-block").hidden = c.portfolio.length === 0;

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


    const masterPhoto = $("#master-photo");
    if (c.master_photo) {
      masterPhoto.hidden = false;
      loadImg(masterPhoto, c.master_photo);
    }
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

    observeReveals(document);
  }

  function openLink(url) {
    if (tg) tg.openLink(url);
    else window.open(url, "_blank");
  }

  document.querySelectorAll("[data-site]").forEach((node) => {
    node.addEventListener("click", () => {
      haptic();
      openLink((state.content && state.content.site) || "https://tattookult.ru");
    });
  });

  $("#map-btn").addEventListener("click", () => {
    const address = (state.content && state.content.address) || "";
    openLink("https://yandex.ru/maps/?text=" + encodeURIComponent(address));
  });

  // ---------- просмотр фото: свайп влево/вправо, тап — закрыть ----------

  const lightbox = $("#lightbox");
  const lbImg = lightbox.querySelector("img");
  let lbIndex = 0;

  function showPhoto(i) {
    const photos = state.content.portfolio;
    lbIndex = (i + photos.length) % photos.length;
    lbImg.style.animation = "none";
    void lbImg.offsetWidth; // перезапуск анимации появления
    lbImg.style.animation = "";
    lbImg.src = photos[lbIndex];
    $("#lb-counter").textContent = photos.length > 1 ? `${lbIndex + 1} / ${photos.length}` : "";
  }
  function openLightbox(i) {
    showPhoto(i);
    lightbox.hidden = false;
    haptic();
  }
  function closeLightbox() { lightbox.hidden = true; }

  let touchX = null;
  let moved = false;
  lightbox.addEventListener("touchstart", (e) => { touchX = e.touches[0].clientX; moved = false; }, { passive: true });
  lightbox.addEventListener("touchmove", (e) => {
    if (touchX === null) return;
    const dx = e.touches[0].clientX - touchX;
    if (Math.abs(dx) > 8) moved = true;
    lbImg.style.transform = `translateX(${dx}px)`;
    lbImg.style.opacity = String(1 - Math.min(Math.abs(dx) / 400, 0.5));
  }, { passive: true });
  lightbox.addEventListener("touchend", (e) => {
    const dx = e.changedTouches[0].clientX - touchX;
    touchX = null;
    lbImg.style.transform = "";
    lbImg.style.opacity = "";
    if (Math.abs(dx) > 60 && state.content.portfolio.length > 1) {
      showPhoto(lbIndex + (dx < 0 ? 1 : -1));
      haptic();
    }
  });
  lightbox.addEventListener("click", () => { if (!moved) closeLightbox(); moved = false; });
  document.addEventListener("keydown", (e) => {
    if (lightbox.hidden) return;
    if (e.key === "Escape") closeLightbox();
    if (e.key === "ArrowRight") showPhoto(lbIndex + 1);
    if (e.key === "ArrowLeft") showPhoto(lbIndex - 1);
  });

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
