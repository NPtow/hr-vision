// Runs only in the rendered search page. Never reads cookies, hidden JS state or network responses.
export function extractPage() {
  const visible = e => !!e && e.getClientRects().length > 0;
  const query = document.querySelector('input[aria-label="Поиск по тексту поста"]')?.value;
  const counter = document.querySelector('[data-role="pscnt"]')?.innerText ?? '';
  const period = [...document.querySelectorAll('[title]')].find(e=>e.title.startsWith('Период выдачи:'))?.title ?? '';
  const rows = [...document.querySelectorAll('.ps-table tbody tr[data-post-id]')].filter(visible).map(row=>({
    channelID:row.getAttribute('data-item-id'),postID:row.getAttribute('data-post-id'),
    channelTitle:row.querySelector('.ps-pcell__ch b')?.innerText ?? '',
    text:row.querySelector('.ps-clip')?.innerText ?? '',
    dateLabel:row.querySelector('td:nth-child(2)')?.getAttribute('title') ?? ''
  }));
  const totalText = counter.match(/Найден(?:о)?\s*[≈~]?\s*([\d\s\u00a0'’]+)/)?.[1];
  const links = [...document.querySelectorAll('nav[aria-label="Страницы выдачи"] a[href]')].filter(visible).map(a=>({text:a.innerText.trim(),url:a.href}));
  // Restrict text checks to the search surface; post contents must not trigger a false quota/CAPTCHA status.
  const heading = document.querySelector('h1')?.innerText ?? '';
  const alerts = [...document.querySelectorAll('[role="alert"],.alert')].filter(e=>visible(e)&&!e.closest('.ps-table,.ps-card')).map(e=>e.innerText).join('\n');
  const challenge = !!document.querySelector('iframe[src*="captcha"], iframe[src*="challenges.cloudflare"], form#challenge-form');
  const emptyMessage = [...document.querySelectorAll('b')].find(e=>visible(e)&&e.innerText==='Ничего не найдено')?.innerText;
  return {url:location.href,query,counter,period,totalCount:totalText?Number(totalText.replace(/\D/g,'')):emptyMessage?0:null,emptyMessage,approximate:/[≈~]/.test(counter),rows,links,heading,alerts,challenge};
}
