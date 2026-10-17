"""Synthetic behavior around the sanitized 2026-10-17 Studio DOM structure."""
from pathlib import Path

BODY = '以下の内容で動画生成を開始してください。\n・ソースが読み込み終わっていること\n・説明動画\n・日本語\n・ペーパークラフト'


def settings_html():
    return Path(__file__).with_name('assets').joinpath('studio_video_settings.html').read_text(encoding='utf8')


def install_studio_fixture(page, settings_html, *, opened=False, delay_ms=0):
    page.set_content('''<section class="source-panel"><div class="single-source-container">
      <button class="source-stretched-button" aria-label="lesson.txt">lesson.txt</button>
      <input type="checkbox" checked aria-label="lesson.txt を選択"></div></section>
      <chat-panel><span>1 個のソース</span><textarea></textarea>
      <button onclick="window.badClicks++">生成</button><button onclick="window.badClicks++">確認</button>
      <button onclick="window.badClicks++">続行</button></chat-panel>
      <section aria-label="レポート"><button onclick="window.badClicks++">生成</button></section>
      <section aria-label="スライド"><button onclick="window.badClicks++">生成</button></section>
      <studio-panel><basic-create-artifact-button><button aria-label="動画解説" id="video">動画解説</button>
      </basic-create-artifact-button><button onclick="window.badClicks++">再試行</button>
      <artifact-library>Studio output will be saved here</artifact-library></studio-panel>''')
    page.evaluate('''({html,opened,delay})=>{
      window.badClicks=0;window.generateClicks=0;window.studioOpens=0;window.chatSends=0;
      window.finishMode='GENERATING';window.suppressGeneration=false;window.ignoreSelection=false;
      function open(){
        if(document.querySelector('[role=dialog]'))return;
        document.body.insertAdjacentHTML('beforeend',html);
        const dialog=document.querySelector('[role=dialog]');
        dialog.querySelectorAll('[role=radiogroup]').forEach((group,i)=>{
          group.querySelectorAll('mat-radio-button').forEach(tile=>{
            const radio=tile.querySelector('input');radio.name='fixture-group-'+i;
            radio.checked=tile.classList.contains('mat-mdc-radio-checked');
            tile.addEventListener('click',()=>{if(window.ignoreSelection){radio.checked=false;return}
              group.querySelectorAll('input').forEach(x=>x.checked=x===radio)});
          });
        });
        const language=dialog.querySelector('[role=combobox]');
        language.onclick=()=>{const box=document.createElement('div');box.setAttribute('role','listbox');
          const option=document.createElement('button');option.setAttribute('role','option');option.textContent='日本語';
          option.onclick=()=>{language.textContent='日本語';box.remove()};box.append(option);document.body.append(box)};
        const toggle=dialog.querySelector('[role=switch]');toggle.onclick=()=>toggle.setAttribute('aria-checked',toggle.getAttribute('aria-checked')==='true'?'false':'true');
        const generate=[...dialog.querySelectorAll('button')].find(e=>e.textContent.trim()==='生成');
        generate.onclick=()=>{window.generateClicks++;if(window.suppressGeneration)return;
          dialog.remove();document.querySelector('[data-testid=fixture-backdrop]')?.remove();
          const library=document.querySelector('artifact-library');
          const card=document.createElement('artifact-library-item');
          if(window.finishMode==='READY')card.innerHTML='<button aria-label="再生">再生</button>';
          else card.textContent='動画を生成しています';library.append(card)};
      }
      window.openStudioFixture=open;
      document.querySelector('#video').onclick=()=>{window.studioOpens++;setTimeout(open,delay)};
      if(opened)open();
    }''', {'html':settings_html,'opened':opened,'delay':delay_ms})
