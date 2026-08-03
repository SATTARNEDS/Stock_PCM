(() => {
    "use strict";

    if (/print|scanner/i.test(window.location.pathname)) return;

    const stylesheetId = "pcmUiEnhancements";
    if (!document.getElementById(stylesheetId)) {
        const stylesheet = document.createElement("link");
        stylesheet.id = stylesheetId;
        stylesheet.rel = "stylesheet";
        stylesheet.href = "/static/ui-enhancements.css";
        document.head.appendChild(stylesheet);
    }

    document.documentElement.classList.add("pcm-ui-ready");

    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    const isMuPage = window.location.pathname.startsWith("/mu/");

    const progress = document.createElement("div");
    progress.className = "pcm-page-progress";
    progress.setAttribute("aria-hidden", "true");
    document.body.appendChild(progress);

    const startProgress = () => {
        progress.className = "pcm-page-progress";
        requestAnimationFrame(() => progress.classList.add("is-active"));
    };

    const completeProgress = () => {
        progress.classList.remove("is-active");
        progress.classList.add("is-complete");
        window.setTimeout(() => {
            progress.className = "pcm-page-progress";
        }, 260);
    };

    window.addEventListener("pageshow", completeProgress);
    window.addEventListener("beforeunload", startProgress);

    if (!reduceMotion.matches) {
        const revealTargets = document.querySelectorAll(
            "main .card:not(.modal .card), main .panel, main section > .table-responsive, .auth-container, .service-card"
        );
        const observer = new IntersectionObserver((entries) => {
            entries.forEach((entry) => {
                if (!entry.isIntersecting) return;
                entry.target.classList.add("pcm-visible");
                observer.unobserve(entry.target);
            });
        }, { threshold: 0.06, rootMargin: "0px 0px -20px" });

        revealTargets.forEach((element, index) => {
            if (element.closest(".modal, .offcanvas") || element.hidden) return;
            element.classList.add("pcm-reveal");
            element.style.transitionDelay = `${Math.min(index * 24, 180)}ms`;
            observer.observe(element);
        });
    }

    if (!isMuPage) {
        document.addEventListener("pointerdown", (event) => {
            if (reduceMotion.matches) return;
            const button = event.target.closest(".btn");
            if (!button || button.disabled) return;

            const rect = button.getBoundingClientRect();
            const size = Math.max(rect.width, rect.height);
            const ripple = document.createElement("span");
            ripple.className = "pcm-ripple";
            ripple.style.width = `${size}px`;
            ripple.style.height = `${size}px`;
            ripple.style.left = `${event.clientX - rect.left - size / 2}px`;
            ripple.style.top = `${event.clientY - rect.top - size / 2}px`;
            button.appendChild(ripple);
            ripple.addEventListener("animationend", () => ripple.remove(), { once: true });
        });
    }
})();
