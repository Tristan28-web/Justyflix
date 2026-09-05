// ==========================================================================
// Netflix Carousel Horizontal Scrolling Function
// ==========================================================================
window.scrollCarousel = function(carouselId, distance) {
  const carousel = document.getElementById(carouselId);
  if (carousel) {
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

  // 2. Clipboard Copy Helper for Direct Download Links
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
});
