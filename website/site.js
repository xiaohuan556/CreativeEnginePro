const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");

function setupMotion() {
  if (reducedMotion.matches) return;

  if ("IntersectionObserver" in window) {
    const observer = new IntersectionObserver((entries) => {
      entries.forEach(({ isIntersecting, target }) => {
        if (!isIntersecting) return;
        target.classList.add("is-visible");
        observer.unobserve(target);
      });
    }, { threshold: 0.08 });
    document.documentElement.classList.add("motion-ready");
    document.querySelectorAll("[data-reveal]").forEach((item) => observer.observe(item));
    reducedMotion.addEventListener("change", () => {
      if (reducedMotion.matches) {
        observer.disconnect();
        document.documentElement.classList.remove("motion-ready");
      }
    });
  }

  const artwork = document.querySelector("[data-parallax]");
  if (!artwork || !matchMedia("(pointer: fine)").matches) return;
  const hero = artwork.closest(".hero");
  hero.addEventListener("pointermove", (event) => {
    if (reducedMotion.matches || event.pointerType === "touch") return;
    const rect = hero.getBoundingClientRect();
    artwork.style.setProperty("--dx", ((event.clientX - rect.left) / rect.width - 0.5) * 21 + "px");
    artwork.style.setProperty("--dy", ((event.clientY - rect.top) / rect.height - 0.5) * 17 + "px");
  });
  hero.addEventListener("pointerleave", () => {
    artwork.style.removeProperty("--dx");
    artwork.style.removeProperty("--dy");
  });
}

function setupTabs() {
  const tablist = document.querySelector("[data-tabs]");
  if (!tablist) return;

  const tabs = Array.from(tablist.querySelectorAll("a"));
  const panels = tabs.map((tab) => document.getElementById(tab.hash.slice(1)));
  tablist.setAttribute("role", "tablist");
  tabs.forEach((tab, index) => {
    tab.setAttribute("role", "tab");
    tab.setAttribute("aria-controls", panels[index].id);
    panels[index].setAttribute("role", "tabpanel");
  });

  function select(index) {
    tabs.forEach((tab, position) => {
      const selected = index === position;
      tab.setAttribute("aria-selected", selected);
      tab.tabIndex = selected ? 0 : -1;
      panels[position].hidden = !selected;
      if (!selected) panels[position].querySelector("video")?.pause();
    });
  }

  function selectFromUrl() {
    const index = tabs.findIndex((tab) => tab.hash === location.hash);
    select(index === -1 ? 0 : index);
  }

  function activate(index) {
    select(index);
    if (location.hash !== tabs[index].hash) history.pushState(null, "", tabs[index].hash);
  }

  tabs.forEach((tab, index) => {
    tab.addEventListener("click", (event) => {
      event.preventDefault();
      activate(index);
    });
    tab.addEventListener("keydown", (event) => {
      const positions = {
        ArrowRight: (index + 1) % tabs.length,
        ArrowLeft: (index - 1 + tabs.length) % tabs.length,
        Home: 0,
        End: tabs.length - 1,
      };
      const next = positions[event.key];
      if (next === undefined) return;
      event.preventDefault();
      activate(next);
      tabs[next].focus();
    });
  });

  window.addEventListener("hashchange", selectFromUrl);
  window.addEventListener("popstate", selectFromUrl);
  selectFromUrl();
}

function setupShowreel() {
  const dialog = document.querySelector("[data-reel-dialog]");
  if (typeof dialog?.showModal !== "function") return;

  const video = dialog.querySelector("video");
  document.querySelectorAll("[data-showreel]").forEach((link) => {
    link.setAttribute("aria-haspopup", "dialog");
    link.addEventListener("click", (event) => {
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      dialog.showModal();
      video.play().catch(() => {});
    });
  });
  dialog.addEventListener("close", () => video.pause());
}

function setupTimeline() {
  const demo = document.querySelector("[data-timeline]");
  if (!demo) return;

  const video = demo.querySelector("video");
  const play = demo.querySelector("[data-play]");
  const overlay = demo.querySelector("[data-overlay]");
  const slider = demo.querySelector("input");
  const time = demo.querySelector("[data-time]");
  const message = demo.querySelector("[data-media-message]");
  const mute = demo.querySelector("[data-mute]");
  const initialDuration = Number(slider.max);

  function formatTime(value) {
    return "00:" + String(Math.floor(value)).padStart(2, "0");
  }

  function render() {
    const playing = !video.paused && !video.ended;
    const duration = Number.isFinite(video.duration) ? video.duration : initialDuration;
    const seconds = Math.floor(video.currentTime);
    const text = formatTime(seconds) + " / " + formatTime(duration);
    const label = seconds + " 秒，共 " + Math.floor(duration) + " 秒";
    if (time.textContent !== text) time.textContent = text;
    if (slider.getAttribute("aria-valuetext") !== label) slider.setAttribute("aria-valuetext", label);
    slider.max = duration;
    slider.value = video.currentTime;
    play.textContent = playing ? "Ⅱ" : "▶";
    play.setAttribute("aria-label", playing ? "暂停演示" : "播放演示");
    overlay.hidden = playing || (!video.ended && video.currentTime > 0);
  }

  async function toggle() {
    if (!video.paused && !video.ended) {
      video.pause();
      return;
    }
    const moveFocus = document.activeElement === overlay;
    try {
      await video.play();
      if (moveFocus && !demo.closest("[hidden]")) play.focus({ preventScroll: true });
    } catch (error) {
      if (error.name !== "AbortError") message.textContent = "样片暂时无法播放";
    }
  }

  video.controls = false;
  demo.querySelectorAll("[data-video-controls]").forEach((controls) => controls.hidden = false);
  play.addEventListener("click", toggle);
  overlay.addEventListener("click", toggle);
  mute.addEventListener("click", () => {
    video.muted = !video.muted;
    mute.setAttribute("aria-pressed", video.muted);
  });
  slider.addEventListener("input", () => {
    video.currentTime = Number(slider.value);
    render();
  });
  ["loadedmetadata", "timeupdate", "play", "pause", "seeked", "ended"].forEach((event) => {
    video.addEventListener(event, render);
  });
  video.addEventListener("error", () => message.textContent = "样片暂时无法播放");
  render();
}

function setupDemos() {
  setupTimeline();
  const compare = document.querySelector("[data-compare]");
  if (compare) {
    const slider = compare.querySelector("input");
    const views = compare.querySelectorAll("[data-view]");
    compare.querySelector(".compare-controls").hidden = false;
    views.forEach((view) => view.addEventListener("click", () => {
      compare.dataset.mode = view.dataset.view;
      slider.closest("label").hidden = view.dataset.view !== "split";
      views.forEach((item) => item.setAttribute("aria-pressed", item === view));
    }));
    slider.addEventListener("input", () => compare.style.setProperty("--split", slider.value + "%"));
  }

  const canvas = document.querySelector("[data-node-demo]");
  if (!canvas) return;
  const nodes = canvas.querySelectorAll("[data-node]");
  const readout = canvas.querySelector(".node-readout");
  nodes.forEach((node) => node.addEventListener("click", () => {
    nodes.forEach((item) => item.setAttribute("aria-pressed", item === node));
    readout.textContent = node.dataset.node;
  }));
}

function setupDownload() {
  const link = document.querySelector("[data-download-link]");
  const release = window.creativeEngineRelease;
  const path = (release?.downloadUrl || release?.installerUrl)?.trim();
  if (!link || !path) return;

  const relativePackage = /^\.\/downloads\/[\w.-]+\.exe$/i.test(path);
  const publicPackage = /^https:\/\//i.test(path);
  if (!relativePackage && !publicPackage) return;

  let url;
  try {
    url = new URL(path, document.baseURI);
  } catch {
    return;
  }
  if (url.username || url.password) return;

  link.href = url.href;
  if (relativePackage) link.download = release.filename || "";
  link.hidden = false;
  document.querySelector("[data-download-pending]").hidden = true;
  document.querySelector("[data-download-status]").textContent = release.size || "Windows 下载";
  if (release.standalone) {
    document.querySelector("[data-download-label]").textContent = "下载 Windows 版";
    document.querySelector("[data-package-type]").textContent = " EXE · 无需安装";
    document.querySelectorAll("[data-standalone]").forEach((item) => item.hidden = false);
    document.querySelectorAll("[data-setup]").forEach((item) => item.hidden = true);
  }
  if (release.notice) {
    const notice = document.querySelector("[data-release-notice]");
    notice.textContent = release.notice;
    notice.hidden = false;
  }
  if (release.version) {
    const version = document.querySelector("[data-version]");
    version.textContent = "· " + release.version;
    version.hidden = false;
  }
  if (/^[a-f\d]{64}$/i.test(release.sha256 || "")) {
    document.querySelector("[data-hash]").textContent = release.sha256;
    document.querySelector("[data-checksum]").hidden = false;
  }
}

setupTabs();
setupShowreel();
setupDemos();
setupDownload();
setupMotion();
