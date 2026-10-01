(() => {
  const tg = window.Telegram ? window.Telegram.WebApp : null;
  const inTelegram = Boolean(tg && tg.initData);
  const $ = (sel) => document.querySelector(sel);
  const state = { content: null, me: null, current: "home", photo: null };

  const haptic = (type) => {
    try { tg && tg.HapticFeedback.impactOccurred(type || "light"); } catch (_) {}
  };
  const notify = (type) => {
    try { tg && tg.HapticFeedback.notificationOccurred(type); } catch (_) {}
  };

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  // ---------- всплывающее уведомление ----------

  const toastBox = $("#toast");
  let toastTimer = null;
  function toast(text) {
    toastBox.textContent = text;
    toastBox.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toastBox.classList.remove("show"), 2600);
  }

  // ---------- навигация ----------

  function go(screen) {
    if (screen === state.current) return;
    document.querySelectorAll(".screen").forEach((node) => node.classList.toggle("active", node.id === screen));
    document.querySelectorAll(".pill").forEach((node) => node.classList.toggle("active", node.dataset.go === screen));
    state.current = screen;
    window.scrollTo(0, 0);
    if (tg) screen === "home" ? tg.BackButton.hide() : tg.BackButton.show();
    if (screen === "me") loadMe();
    haptic();
  }

  document.addEventListener("click", (e) => {
    const target = e.target.closest("[data-go]");
    if (target) {
      e.preventDefault();
      go(target.dataset.go);
    }
  });

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

  // ---------- анимации ----------

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

  // ---------- контент студии ----------

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
      loadImg(img, src);
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
    $("#footer-address").textContent = c.address;
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
  function openMap() {
    const c = state.content || {};
    if (c.map_url) return openLink(c.map_url);
    openLink("https://yandex.ru/maps/?text=" + encodeURIComponent(c.address || "Владивосток, Светланская ул., 23"));
  }
  // Чат с мастером: открываем личку и подставляем начало сообщения — клиенту остаётся дописать и отправить
  function greeting() {
    const master = state.content && state.content.master;
    return `Здравствуйте${master ? ", " + master : ""}!`;
  }
  function openChat(draft, topic) {
    const c = state.content || {};
    const user = c.master_chat || c.bot;
    if (!user) return;
    // Предупреждаем мастера в боте, кто сейчас напишет. keepalive — запрос дойдёт, даже если приложение свернётся
    if (inTelegram) {
      fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ initData: tg.initData, topic: topic || "" }),
        keepalive: true,
      }).catch(() => {});
    }
    const text = draft || `${greeting()} У меня вопрос по татуировке: `;
    const url = `https://t.me/${user}?text=${encodeURIComponent(text)}`;
    if (tg && tg.openTelegramLink) tg.openTelegramLink(url); else openLink(url);
  }

  document.addEventListener("click", (e) => {
    if (e.target.closest("[data-site]")) {
      haptic();
      openLink((state.content && state.content.site) || "https://tattookult.ru");
    } else if (e.target.closest(".map-btn")) {
      haptic();
      openMap();
    } else if (e.target.closest(".chat-btn")) {
      haptic();
      const btn = e.target.closest(".chat-btn");
      openChat(btn.dataset.draft, btn.dataset.topic);
    }
  });

  // ---------- мои записи ----------

  function meRequest() {
    return fetch("/api/me", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ initData: tg ? tg.initData : "" }),
    }).then((r) => (r.ok ? r.json() : Promise.reject(r)));
  }

  function renderNextSession(me) {
    const next = me && me.upcoming[0];
    $("#next-session").hidden = !next;
    $("#me-dot").hidden = !next;
    if (!next) return;
    $("#ns-day").textContent = next.day;
    $("#ns-month").textContent = next.month.slice(0, 3);
    $("#ns-when").textContent = `${next.weekday}, ${next.time} · мастер ${(state.content && state.content.master) || ""}`.trim();
    observeReveals(document);
  }

  function sessionCard(s) {
    const card = el("div", "session");
    const date = el("div", "session-date");
    date.append(el("b", null, String(s.day)), el("small", null, s.month.slice(0, 3)));
    const body = el("div", "session-body");
    body.append(el("p", "eyebrow", "Ближайший сеанс"), el("p", "session-time", `${s.weekday}, ${s.time}`));
    if (state.content) body.append(el("p", "session-place", state.content.address));
    const actions = el("div", "session-actions");
    const map = el("button", "chip-btn map-btn", "📍 Как добраться");
    const chat = el("button", "chip-btn chat-btn", "💬 Написать");
    chat.dataset.draft = `${greeting()} Я записан(а) на сеанс ${s.day} ${s.month} в ${s.time}. Вопрос: `;
    chat.dataset.topic = `${s.day} ${s.month} в ${s.time}`;
    actions.append(map, chat);
    body.append(actions);
    card.append(date, body);
    return card;
  }

  const PAY = {
    paid: ["✅", "Предоплата получена"],
    claimed: ["🔎", "Проверяем поступление"],
    pending: ["⏳", "Ждём предоплату — реквизиты в чате с ботом"],
  };
  const REQ = { new: "Новая — мастер скоро свяжется", contacted: "Мастер на связи — обсуждаете детали" };

  function renderMe(me) {
    const box = $("#me-content");
    box.replaceChildren();
    if (me.name) $("#me-title").textContent = `${me.name.split(" ")[0]}, ваши записи`;

    if (me.upcoming.length) {
      me.upcoming.forEach((s, i) => {
        const card = sessionCard(s);
        if (i > 0) card.querySelector(".eyebrow").textContent = "Следующий сеанс";
        box.append(card);
      });
    }

    if (me.payments.length) {
      const card = el("div", "card");
      card.append(el("p", "label", "Предоплата"));
      me.payments.forEach((p) => {
        const [icon, text] = PAY[p.status] || ["•", p.status];
        const row = el("div", "row");
        row.append(el("span", "row-icon", icon), el("span", "row-text", `${p.amount} · ${text}`));
        card.append(row);
      });
      box.append(card);
    }

    if (me.requests.length) {
      const card = el("div", "card");
      card.append(el("p", "label", "Заявки"));
      me.requests.forEach((r) => {
        const row = el("div", "row");
        row.append(el("span", "row-icon", "📝"), el("span", "row-text", `№${r.id} от ${r.date} · ${REQ[r.status] || ""}`));
        card.append(row);
      });
      box.append(card);
    }

    if (!me.upcoming.length && !me.requests.length) {
      const empty = el("div", "empty-state");
      empty.append(
        el("p", "empty-icon", "✦"),
        el("p", "empty-title", "Пока нет записей"),
        el("p", "empty-text", "Оставьте заявку — после консультации здесь появится дата вашего сеанса."),
      );
      const btn = el("button", "cta", "Записаться");
      btn.dataset.go = "form";
      empty.append(btn);
      box.append(empty);
    }

    if (me.aftercare) {
      const d = el("details", "card aftercare");
      d.append(el("summary", null, "🩹 Памятка по уходу за татуировкой"), el("p", null, me.aftercare));
      d.open = me.past.length > 0 && !me.upcoming.length;
      box.append(d);
    }

    if (me.past.length) {
      const card = el("div", "card");
      card.append(el("p", "label", "История"));
      me.past.forEach((s) => {
        const row = el("div", "row");
        row.append(el("span", "row-icon", "✓"), el("span", "row-text", s.when));
        card.append(row);
      });
      box.append(card);
    }

    const chat = el("button", "link-btn chat-btn center", "💬 Задать вопрос в чате");
    box.append(chat);
  }

  function renderMeUnavailable() {
    const box = $("#me-content");
    box.replaceChildren();
    const empty = el("div", "empty-state");
    empty.append(
      el("p", "empty-icon", "✦"),
      el("p", "empty-title", "Откройте через Telegram"),
      el("p", "empty-text", "Записи видны, когда приложение открыто из бота студии."),
    );
    box.append(empty);
  }

  function loadMe() {
    if (!inTelegram) return renderMeUnavailable();
    meRequest()
      .then((me) => { state.me = me; renderMe(me); renderNextSession(me); })
      .catch(() => {
        if (!state.me) renderMeUnavailable();
      });
  }

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

  // ---------- форма ----------

  const form = $("#apply-form");
  const errorBox = $("#form-error");
  const submitBtn = $("#submit-btn");

  const tgUser = tg && tg.initDataUnsafe && tg.initDataUnsafe.user;
  if (tgUser) form.name.value = [tgUser.first_name, tgUser.last_name].filter(Boolean).join(" ");

  // Телефон: аккуратный формат +7 (999) 123-45-67 по мере ввода
  function formatPhone(value) {
    let d = value.replace(/\D/g, "");
    if (!d) return "";
    if (d[0] === "8") d = "7" + d.slice(1);
    if (d[0] !== "7") d = "7" + d;
    d = d.slice(0, 11);
    let out = "+7";
    if (d.length > 1) out += " (" + d.slice(1, 4);
    if (d.length >= 4) out += ")";
    if (d.length > 4) out += " " + d.slice(4, 7);
    if (d.length > 7) out += "-" + d.slice(7, 9);
    if (d.length > 9) out += "-" + d.slice(9, 11);
    return out;
  }
  form.phone.addEventListener("input", (e) => {
    const input = e.target;
    const atEnd = input.selectionStart === input.value.length;
    if (atEnd && e.inputType !== "deleteContentBackward") input.value = formatPhone(input.value);
  });
  form.phone.addEventListener("blur", () => { form.phone.value = formatPhone(form.phone.value); });

  // Номер из Telegram одной кнопкой
  const tgPhoneBtn = $("#tg-phone");
  if (inTelegram && tg.requestContact) {
    tgPhoneBtn.hidden = false;
    tgPhoneBtn.addEventListener("click", () => {
      tg.requestContact((shared, response) => {
        const phone = response && response.responseUnsafe && response.responseUnsafe.contact
          && response.responseUnsafe.contact.phone_number;
        if (phone) {
          form.phone.value = formatPhone(phone);
          notify("success");
        } else if (shared) {
          toast("Номер отправлен студии — можно ввести его и здесь");
        }
      });
    });
  }

  // Быстрые варианты: один выбор в группе, повторное нажатие снимает выбор
  const details = {};
  document.querySelectorAll(".chips").forEach((group) => {
    group.addEventListener("click", (e) => {
      const chip = e.target.closest("button");
      if (!chip) return;
      const key = group.dataset.group;
      const selected = chip.classList.contains("on");
      group.querySelectorAll("button").forEach((b) => b.classList.remove("on"));
      if (selected) delete details[key];
      else { chip.classList.add("on"); details[key] = chip.textContent; }
      haptic();
    });
  });

  // Референс: уменьшаем фото прямо в телефоне, чтобы отправка была быстрой
  const photoInput = $("#photo-input");
  function resizeImage(file, maxSide = 1600, quality = 0.85) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onerror = reject;
      reader.onload = () => {
        const img = new Image();
        img.onerror = reject;
        img.onload = () => {
          const scale = Math.min(1, maxSide / Math.max(img.width, img.height));
          const canvas = document.createElement("canvas");
          canvas.width = Math.round(img.width * scale);
          canvas.height = Math.round(img.height * scale);
          canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
          resolve(canvas.toDataURL("image/jpeg", quality));
        };
        img.src = reader.result;
      };
      reader.readAsDataURL(file);
    });
  }
  function setPhoto(dataUrl) {
    state.photo = dataUrl;
    $("#upload-empty").hidden = Boolean(dataUrl);
    $("#upload-preview").hidden = !dataUrl;
    $("#upload").classList.toggle("filled", Boolean(dataUrl));
    if (dataUrl) $("#upload-img").src = dataUrl;
    else photoInput.value = "";
  }
  photoInput.addEventListener("change", async () => {
    const file = photoInput.files && photoInput.files[0];
    if (!file) return;
    try {
      setPhoto(await resizeImage(file));
      haptic();
    } catch (_) {
      toast("Не получилось открыть фото — попробуйте другое");
    }
  });
  $("#upload-remove").addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    setPhoto(null);
  });

  function showError(text) {
    errorBox.textContent = text;
    errorBox.hidden = !text;
    if (text) notify("error");
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
      details,
      photo: state.photo,
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
      notify("success");
      const master = (state.content && state.content.master) || "Мастер";
      $("#success-text").textContent = `${master} свяжется с вами в ближайшее время.`;
      form.idea.value = "";
      setPhoto(null);
      document.querySelectorAll(".chips .on").forEach((b) => b.classList.remove("on"));
      Object.keys(details).forEach((k) => delete details[k]);
      state.me = null;
      go("success");
    } catch (err) {
      showError(err.message || "Нет соединения. Попробуйте ещё раз");
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = "Отправить заявку";
    }
  });

  // ---------- загрузка ----------

  fetch("/api/content")
    .then((r) => r.json())
    .then((c) => {
      state.content = c;
      render(c);
      if (inTelegram) meRequest().then((me) => { state.me = me; renderNextSession(me); }).catch(() => {});
    })
    .catch(() => toast("Нет соединения — проверьте интернет"));
})();
