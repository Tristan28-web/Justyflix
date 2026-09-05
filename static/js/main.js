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

  // 3. Multi-Page Deep Crawl Trigger & Poller
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
        setTimeout(() => window.location.reload(), 2500);
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
      if (statusText) statusText.textContent = `Initiating crawl for ${numPages} page(s) starting from page ${startPage}...`;

      try {
        const resp = await fetch(`/api/crawl?start_page=${startPage}&num_pages=${numPages}&concurrency=4`, {
          method: 'POST'
        });
        const result = await resp.json();

        if (pollInterval) clearInterval(pollInterval);
        pollInterval = setInterval(pollCrawlerStatus, 2500);
      } catch (err) {
        alert('Failed to start crawler: ' + err.message);
        startCrawlBtn.disabled = false;
        startCrawlBtn.innerHTML = '<i class="bi bi-play-circle-fill me-1"></i> Start Multi-Page Crawl';
      }
    });
  }
});
