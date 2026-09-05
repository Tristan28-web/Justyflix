// Netflix Carousel Horizontal Scrolling Function
// ==========================================================================
window.scrollCarousel = function(carouselId, distance) {
  const carousel = document.getElementById(carouselId);
  if (carousel) {
    carousel._manualHoldUntil = Date.now() + 7000; // Hold auto-sliding for 7s on manual arrow click
    carousel.scrollBy({
      left: distance,
      behavior: 'smooth'
    });
  }
};

document.addEventListener('DOMContentLoaded', () => {
  // 1. Netflix Navbar Scroll Transparency Effect
  const navbar = document.getElementById('netflixNavbar');
  if (navbar) {
    const handleScroll = () => {
      if (window.scrollY > 40) {
        navbar.classList.add('scrolled');
      } else {
        navbar.classList.remove('scrolled');
      }
    };
    window.addEventListener('scroll', handleScroll, { passive: true });
    handleScroll(); // Initial check
  }

  // 2. Clipboard Copy Helper for Download Links
  const copyButtons = document.querySelectorAll('.btn-copy-link');
  copyButtons.forEach((btn) => {
    btn.addEventListener('click', async () => {
      const textToCopy = btn.getAttribute('data-link');
      if (!textToCopy) return;

      try {
        await navigator.clipboard.writeText(textToCopy);
        const originalHtml = btn.innerHTML;
        btn.innerHTML = '<i class="bi bi-check-lg text-success"></i> Copied!';

        setTimeout(() => {
          btn.innerHTML = originalHtml;
        }, 2000);
      } catch (err) {
        console.error('Copy failed:', err);
      }
    });
  });

  // 3. Crawler Control Poller (for /scrape admin view)
  const startCrawlBtn = document.getElementById('btnStartDeepCrawl');
  const progressArea = document.getElementById('crawlerProgressArea');
  const statusText = document.getElementById('crawlerStatusText');
  const percentText = document.getElementById('crawlerPercentText');
  const progressBar = document.getElementById('crawlerProgressBar');

  let pollInterval = null;

  const pollCrawlerStatus = async () => {
    try {
      const res = await fetch('/api/crawl/status');
      const data = await res.json();
      const crawler = data.crawler;

      if (crawler && statusText) {
        statusText.textContent = crawler.message;
      }

      if (crawler && !crawler.is_running) {
        if (pollInterval) clearInterval(pollInterval);
        if (startCrawlBtn) {
          startCrawlBtn.disabled = false;
          startCrawlBtn.innerHTML = '<i class="bi bi-play-circle-fill me-1"></i> Start Multi-Page Crawl';
        }
        if (percentText) percentText.textContent = 'Finished!';
        if (progressBar) {
          progressBar.classList.remove('progress-bar-animated');
          progressBar.classList.add('bg-success');
        }
        setTimeout(() => window.location.reload(), 2000);
      }
    } catch (e) {
      console.error('Error polling crawler status:', e);
    }
  };

  if (startCrawlBtn) {
    startCrawlBtn.addEventListener('click', async () => {
      const startPage = document.getElementById('crawlStartPage')?.value || 1;
      const numPages = document.getElementById('crawlNumPages')?.value || 3;

      startCrawlBtn.disabled = true;
      startCrawlBtn.innerHTML = `
        <span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>
        Crawling MWLBD Catalog...
      `;

      if (progressArea) progressArea.style.display = 'block';
      if (statusText) statusText.textContent = `Initiating crawl for ${numPages} page(s)...`;

      try {
        const resp = await fetch(`/api/crawl?start_page=${startPage}&num_pages=${numPages}&concurrency=4`, {
          method: 'POST'
        });
        await resp.json();

        if (pollInterval) clearInterval(pollInterval);
        pollInterval = setInterval(pollCrawlerStatus, 2000);
      } catch (err) {
        alert('Failed to start crawler: ' + err.message);
        startCrawlBtn.disabled = false;
        startCrawlBtn.innerHTML = '<i class="bi bi-play-circle-fill me-1"></i> Start Multi-Page Crawl';
      }
    });
  }

  // ==========================================================================
  // 4. Interactive Real-Time Notification Bell & Live Detection
  // ==========================================================================
  const notificationBellBtn = document.getElementById('notificationBellBtn');
  const notificationBellIcon = document.getElementById('notificationBellIcon');
  const notificationBadge = document.getElementById('notificationBadge');
  const notificationHeaderBadge = document.getElementById('notificationHeaderBadge');
  const notificationList = document.getElementById('notificationList');
  const markAllBtn = document.getElementById('markAllNotificationsReadBtn');
  const toastContainer = document.getElementById('notificationToastContainer');

  const STORAGE_SEEN_KEY = 'justyflix_seen_movie_ids_v1';
  const STORAGE_LAST_SCRAPE_KEY = 'justyflix_last_scrape_ts';

  let currentNotifications = [];
  let isCheckingNotifications = false;

  const getSeenIds = () => {
    try {
      return JSON.parse(localStorage.getItem(STORAGE_SEEN_KEY) || '[]');
    } catch (e) {
      return [];
    }
  };

  const saveSeenIds = (ids) => {
    try {
      localStorage.setItem(STORAGE_SEEN_KEY, JSON.stringify(ids));
    } catch (e) {}
  };

  const showLiveToast = (movie) => {
    if (!toastContainer) return;
    const toast = document.createElement('div');
    toast.className = 'netflix-live-toast';
    toast.innerHTML = `
      <img src="${movie.poster || ''}" class="notification-thumb" onerror="this.src='https://placehold.co/44x62/1f1f1f/ffffff?text=2026';">
      <div class="flex-grow-1 overflow-hidden">
        <div class="d-flex align-items-center justify-content-between mb-1">
          <span class="badge bg-danger" style="font-size: 0.65rem;">${movie.badge_text || 'New Release'}</span>
          <span class="text-muted small" style="font-size: 0.7rem;">Just now</span>
        </div>
        <div class="text-white fw-bold text-truncate" style="font-size: 0.85rem;">${movie.title}</div>
        <div class="text-muted small mt-1 d-flex align-items-center gap-1">
          <span class="text-success fw-semibold"><i class="bi bi-check-circle me-1"></i>Available</span>
          <span class="mx-1">•</span>
          <a href="${movie.url}" class="text-info text-decoration-none fw-bold ms-auto">Stream Now &raquo;</a>
        </div>
      </div>
      <button type="button" class="btn-close btn-close-white small ms-2" aria-label="Close" onclick="this.parentElement.remove()"></button>
    `;
    toastContainer.appendChild(toast);

    setTimeout(() => {
      if (toast.parentElement) {
        toast.style.transition = 'opacity 0.5s ease, transform 0.5s ease';
        toast.style.opacity = '0';
        toast.style.transform = 'translateX(100%)';
        setTimeout(() => toast.remove(), 500);
      }
    }, 8000);
  };

  const renderNotificationDrawer = (notifications) => {
    if (!notificationList) return;
    const seenIds = getSeenIds();
    const unreadItems = notifications.filter(n => !seenIds.includes(n.id));
    const unreadCount = unreadItems.length;

    // Update bell pill badge
    if (notificationBadge) {
      if (unreadCount > 0) {
        notificationBadge.textContent = unreadCount > 99 ? '99+' : unreadCount;
        notificationBadge.style.display = 'inline-block';
      } else {
        notificationBadge.style.display = 'none';
      }
    }

    // Update drawer header badge
    if (notificationHeaderBadge) {
      notificationHeaderBadge.textContent = `${unreadCount} New`;
    }

    if (!notifications || notifications.length === 0) {
      notificationList.innerHTML = `
        <div class="p-4 text-center text-secondary small">
          <i class="bi bi-bell-slash fs-4 d-block mb-2 text-muted"></i>
          No new release notifications yet.
        </div>
      `;
      return;
    }

    let html = '';
    notifications.forEach(item => {
      const isUnread = !seenIds.includes(item.id);
      const isAvailable = item.status === 'available';
      html += `
        <a href="${item.url}" class="notification-card-item ${isUnread ? 'is-unread' : ''}">
          <img
            src="${item.poster || ''}"
            alt="${item.title}"
            class="notification-thumb"
            onerror="this.onerror=null; this.src='https://placehold.co/44x62/222/fff?text=2026';"
          >
          <div class="flex-grow-1 overflow-hidden">
            <div class="d-flex align-items-center justify-content-between mb-1">
              <span class="badge ${item.is_future ? 'bg-danger' : 'bg-secondary'}" style="font-size: 0.62rem;">
                ${item.badge_text}
              </span>
              <span class="text-muted" style="font-size: 0.68rem;">${item.time_ago}</span>
            </div>
            <div class="notification-title" title="${item.title}">
              ${item.title}
            </div>
            <div class="notification-meta">
              <span class="text-warning"><i class="bi bi-star-fill me-1" style="font-size: 0.65rem;"></i>${item.rating}</span>
              <span>•</span>
              <span class="text-light">${item.year}</span>
              ${isAvailable ? '<span class="badge bg-success ms-auto" style="font-size: 0.6rem;"><i class="bi bi-check-circle-fill me-1"></i>Available</span>' : ''}
            </div>
          </div>
        </a>
      `;
    });

    notificationList.innerHTML = html;
  };

  const fetchAndCheckNotifications = async (isInitial = false) => {
    if (isCheckingNotifications) return;
    isCheckingNotifications = true;
    try {
      const res = await fetch('/api/notifications');
      if (!res.ok) return;
      const data = await res.json();
      if (data.status !== 'success') return;

      const newNotifications = data.notifications || [];
      const prevScrape = localStorage.getItem(STORAGE_LAST_SCRAPE_KEY);
      const currentScrape = data.last_scrape;

      const seenIds = getSeenIds();

      if (isInitial && (!localStorage.getItem(STORAGE_SEEN_KEY) || seenIds.length === 0)) {
        // Keep top 3 as unread for initial discovery
        const initialSeen = newNotifications.slice(3).map(n => n.id);
        saveSeenIds(initialSeen);
        localStorage.setItem(STORAGE_LAST_SCRAPE_KEY, currentScrape || '');
      } else if (!isInitial && currentNotifications.length > 0) {
        // Detect newly scraped movies
        const existingIds = new Set(currentNotifications.map(n => n.id));
        const brandNewMovies = newNotifications.filter(n => !existingIds.has(n.id) && !seenIds.includes(n.id));

        if (brandNewMovies.length > 0) {
          // Trigger Bell Shake/Ring animation
          if (notificationBellIcon) {
            notificationBellIcon.classList.remove('bell-ringing');
            void notificationBellIcon.offsetWidth;
            notificationBellIcon.classList.add('bell-ringing');
          }

          // Show floating live alert toast for the latest brand-new release
          showLiveToast(brandNewMovies[0]);
        }
      }

      currentNotifications = newNotifications;
      if (currentScrape) {
        localStorage.setItem(STORAGE_LAST_SCRAPE_KEY, currentScrape);
      }

      renderNotificationDrawer(currentNotifications);
    } catch (err) {
      console.error('Error fetching notifications:', err);
    } finally {
      isCheckingNotifications = false;
    }
  };

  // Mark all notifications as read
  if (markAllBtn) {
    markAllBtn.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      const allIds = currentNotifications.map(n => n.id);
      saveSeenIds(allIds);
      renderNotificationDrawer(currentNotifications);
    });
  }

  // Initial fetch and 20s interval polling for newly scraped releases
  fetchAndCheckNotifications(true);
  setInterval(() => {
    fetchAndCheckNotifications(false);
  }, 20000);

  // ==========================================================================
  // 5. Cinematic Hero Billboard Slider (Alternating Featured Movies with WOW Animation)
  // ==========================================================================
  const initHeroBillboardSlider = () => {
    const slider = document.getElementById('netflixHeroSlider');
    if (!slider) return;

    const slides = Array.from(slider.querySelectorAll('.netflix-hero-slide'));
    const indicators = Array.from(slider.querySelectorAll('.netflix-hero-indicator'));
    const prevBtn = document.getElementById('heroPrevBtn');
    const nextBtn = document.getElementById('heroNextBtn');

    if (slides.length <= 1) return;

    let currentIndex = 0;
    let autoSlideInterval = null;
    let isHovered = false;
    const SLIDE_DURATION = 6500; // 6.5s per featured title

    const restartIndicatorProgress = (elem) => {
      if (!elem) return;
      const prog = elem.querySelector('.indicator-progress');
      if (prog) {
        prog.style.animation = 'none';
        void prog.offsetWidth; // Force reflow to re-trigger keyframe animation
        prog.style.animation = '';
      }
    };

    const activateSlide = (newIndex) => {
      if (newIndex === currentIndex) return;

      // Deactivate current slide & indicator
      slides[currentIndex].classList.remove('active');
      if (indicators[currentIndex]) {
        indicators[currentIndex].classList.remove('active');
      }

      currentIndex = (newIndex + slides.length) % slides.length;

      // Activate new slide & indicator
      slides[currentIndex].classList.add('active');
      if (indicators[currentIndex]) {
        indicators[currentIndex].classList.add('active');
        restartIndicatorProgress(indicators[currentIndex]);
      }
    };

    const advanceNext = () => {
      activateSlide(currentIndex + 1);
    };

    const advancePrev = () => {
      activateSlide(currentIndex - 1);
    };

    const startTimer = () => {
      if (autoSlideInterval) clearInterval(autoSlideInterval);
      autoSlideInterval = setInterval(() => {
        if (!isHovered && !document.hidden) {
          advanceNext();
        }
      }, SLIDE_DURATION);
    };

    // Hover pauses auto-slide and progress animation
    slider.addEventListener('mouseenter', () => {
      isHovered = true;
    });
    slider.addEventListener('mouseleave', () => {
      isHovered = false;
    });

    // Touch support for mobile devices
    slider.addEventListener('touchstart', () => {
      isHovered = true;
    }, { passive: true });
    slider.addEventListener('touchend', () => {
      setTimeout(() => { isHovered = false; }, 2500);
    }, { passive: true });

    // Indicators click to jump directly
    indicators.forEach((indicator, idx) => {
      indicator.addEventListener('click', (e) => {
        e.preventDefault();
        activateSlide(idx);
        startTimer();
      });
    });

    // Prev / Next arrow buttons
    if (prevBtn) {
      prevBtn.addEventListener('click', (e) => {
        e.preventDefault();
        advancePrev();
        startTimer();
      });
    }

    if (nextBtn) {
      nextBtn.addEventListener('click', (e) => {
        e.preventDefault();
        advanceNext();
        startTimer();
      });
    }

    // Page Visibility API: pause when tab hidden, resume when visible
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) {
        if (autoSlideInterval) clearInterval(autoSlideInterval);
      } else {
        startTimer();
      }
    });

    // Start auto timer
    startTimer();
  };

  // ==========================================================================
  // 6. Presentation Movie Carousel Horizontal Auto-Sliding (Smooth & Lightweight)
  // ==========================================================================
  const initPresentationCarousels = () => {
    const carousels = document.querySelectorAll('.netflix-carousel');
    if (!carousels.length) return;

    // Use IntersectionObserver so off-screen carousels do not execute scroll calculations
    const visibleCarousels = new Set();
    const observer = new IntersectionObserver((entries) => {
      entries.forEach(entry => {
        if (entry.isIntersecting) {
          visibleCarousels.add(entry.target);
        } else {
          visibleCarousels.delete(entry.target);
        }
      });
    }, { threshold: 0.15 });

    carousels.forEach((carousel, idx) => {
      observer.observe(carousel);

      let isPaused = false;
      const container = carousel.closest('.netflix-carousel-container') || carousel;

      container.addEventListener('mouseenter', () => { isPaused = true; });
      container.addEventListener('mouseleave', () => { isPaused = false; });
      container.addEventListener('touchstart', () => { isPaused = true; }, { passive: true });
      container.addEventListener('touchend', () => {
        setTimeout(() => { isPaused = false; }, 3000);
      }, { passive: true });

      // Stagger intervals across carousels (e.g., 4.2s, 4.8s, 5.3s) for natural presentation flow
      const intervalDuration = 4200 + ((idx * 650) % 2000);

      setInterval(() => {
        if (document.hidden) return; // Tab not focused
        if (isPaused) return; // User hover/touch
        if (carousel._manualHoldUntil && Date.now() < carousel._manualHoldUntil) return; // Recently clicked arrows
        if (!visibleCarousels.has(carousel)) return; // Out of viewport

        // Calculate smooth slide step (~2 to 3 cards width)
        const step = Math.max(280, Math.floor(carousel.clientWidth * 0.68));
        const maxScroll = carousel.scrollWidth - carousel.clientWidth;

        if (carousel.scrollLeft >= maxScroll - 20) {
          // Reached end: loop smoothly back to beginning
          carousel.scrollTo({ left: 0, behavior: 'smooth' });
        } else {
          carousel.scrollBy({ left: step, behavior: 'smooth' });
        }
      }, intervalDuration);
    });
  };

  initHeroBillboardSlider();
  initPresentationCarousels();
});
