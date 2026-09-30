/* Lightweight UI helpers & Admin Modal Controller */

(function () {
  // Flash animation
  document.querySelectorAll(".flash").forEach(function (el) {
    el.style.animation = "rise 0.4s ease-out both";
  });

  // Admin Modal controller
  var modal = document.getElementById("admin-login-modal");
  var openBtn = document.getElementById("open-admin-modal-btn");
  var closeBtn = document.getElementById("close-admin-modal-btn");
  var pwToggleBtn = document.getElementById("modal-toggle-pw");
  var pwInput = document.getElementById("modal-admin-password");

  if (modal) {
    function showModal() {
      if (typeof modal.showModal === "function") {
        modal.showModal();
      } else {
        modal.setAttribute("open", "true");
      }
    }

    function hideModal() {
      if (typeof modal.close === "function") {
        modal.close();
      } else {
        modal.removeAttribute("open");
      }
    }

    if (openBtn) {
      openBtn.addEventListener("click", function (e) {
        e.preventDefault();
        showModal();
      });
    }

    if (closeBtn) {
      closeBtn.addEventListener("click", function () {
        hideModal();
      });
    }

    // Close when clicking dialog backdrop
    modal.addEventListener("click", function (e) {
      var rect = modal.getBoundingClientRect();
      var isInDialog =
        rect.top <= e.clientY &&
        e.clientY <= rect.top + rect.height &&
        rect.left <= e.clientX &&
        e.clientX <= rect.left + rect.width;
      if (!isInDialog) {
        hideModal();
      }
    });

    if (pwToggleBtn && pwInput) {
      pwToggleBtn.addEventListener("click", function (e) {
        e.preventDefault();
        var isPw = pwInput.type === "password";
        pwInput.type = isPw ? "text" : "password";
        pwToggleBtn.textContent = isPw ? "🙈" : "👁️";
      });
    }

    // Auto open modal only on /admin/login page
    if (document.querySelector(".page-admin-login")) {
      showModal();
    }
  }

  // Prevent double-submit on forms
  document.querySelectorAll("form").forEach(function (form) {
    if (form.id === "guru-login-form") return;

    form.addEventListener("submit", function () {
      var btn = form.querySelector('button[type="submit"]');
      if (!btn || btn.disabled) return;
      var original = btn.textContent;
      setTimeout(function () {
        btn.disabled = true;
        btn.dataset.originalText = original;
        btn.textContent = "Memproses…";
      }, 0);
      setTimeout(function () {
        btn.disabled = false;
        btn.textContent = btn.dataset.originalText || original || "Kirim";
      }, 5000);
    });
  });
})();
