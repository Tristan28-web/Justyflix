document.addEventListener('DOMContentLoaded', () => {
  // 1. Real-time Search Filtering
  const searchInput = document.getElementById('movieSearchInput');
  const movieCards = document.querySelectorAll('.movie-grid-item');
  const emptyState = document.getElementById('noMoviesFound');

  if (searchInput && movieCards.length > 0) {
    searchInput.addEventListener('input', (e) => {
      const term = e.target.value.toLowerCase().trim();
      let visibleCount = 0;

      movieCards.forEach((card) => {
        const title = card.getAttribute('data-title') || '';
        const genre = card.getAttribute('data-genre') || '';
        const year = card.getAttribute('data-year') || '';

        const match = title.includes(term) || genre.includes(term) || year.includes(term);
        if (match) {
          card.style.display = '';
          visibleCount++;
        } else {
          card.style.display = 'none';
        }
      });

      if (emptyState) {
        emptyState.style.display = visibleCount === 0 ? 'block' : 'none';
      }
    });
  }

  // 2. Clipboard Copy Helper
  const copyButtons = document.querySelectorAll('.btn-copy-link');
  copyButtons.forEach((btn) => {
    btn.addEventListener('click', async () => {
      const textToCopy = btn.getAttribute('data-link');
      if (!textToCopy) return;

      try {
        await navigator.clipboard.writeText(textToCopy);
        const originalHtml = btn.innerHTML;
        btn.innerHTML = '<i class="bi bi-check-lg"></i> Copied!';
        btn.classList.add('btn-success');
        btn.classList.remove('btn-outline-info', 'btn-outline-secondary');

        setTimeout(() => {
          btn.innerHTML = originalHtml;
          btn.classList.remove('btn-success');
          btn.classList.add('btn-outline-info');
        }, 2000);
      } catch (err) {
        console.error('Copy failed:', err);
      }
    });
  });

  // 3. Manual Scrape Trigger via AJAX
  const triggerScrapeBtn = document.getElementById('btnTriggerScrape');
  const scrapeStatusBox = document.getElementById('scrapeStatusBox');

  if (triggerScrapeBtn) {
    triggerScrapeBtn.addEventListener('click', async () => {
      const limitInput = document.getElementById('scrapeLimitInput');
      const limit = limitInput ? limitInput.value : 15;

      triggerScrapeBtn.disabled = true;
      const originalText = triggerScrapeBtn.innerHTML;
      triggerScrapeBtn.innerHTML = `
        <span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>
        Scraping MWLBD...
      `;

      if (scrapeStatusBox) {
        scrapeStatusBox.innerHTML = `
          <div class="alert alert-info py-2 mb-3">
            <i class="bi bi-arrow-repeat spin-icon"></i> Background scrape initiated for top ${limit} movies. This may take 15-30 seconds...
          </div>
        `;
      }

      try {
        const resp = await fetch(`/api/scrape?limit=${limit}`, { method: 'POST' });
        const result = await resp.json();

        if (scrapeStatusBox) {
          scrapeStatusBox.innerHTML = `
            <div class="alert alert-success py-2 mb-3">
              <i class="bi bi-check-circle-fill me-2"></i> ${result.message || 'Scrape completed! Refreshing listing in 3 seconds...'}
            </div>
          `;
        }

        setTimeout(() => {
          window.location.reload();
        }, 3500);
      } catch (err) {
        if (scrapeStatusBox) {
          scrapeStatusBox.innerHTML = `
            <div class="alert alert-danger py-2 mb-3">
              <i class="bi bi-exclamation-triangle-fill me-2"></i> Scrape request failed: ${err.message}
            </div>
          `;
        }
        triggerScrapeBtn.disabled = false;
        triggerScrapeBtn.innerHTML = originalText;
      }
    });
  }
});
