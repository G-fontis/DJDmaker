"""Disposable Notebook home with the observed sort menu/CDK relationship."""
HOME = 'https://notebook.google.com/'
NOTEBOOK_ID = '00000000-0000-4000-8000-000000000211'
NOTEBOOK_URL = HOME+'notebook/'+NOTEBOOK_ID


def install_sort_fixture(page, *, opened=True, ignore_escape=False, backdrop_delay=0):
    page.set_content('''<main><h1>Notebook home fixture</h1>
      <button aria-label="Sort notebooks" aria-controls="sort-menu" aria-expanded="false" id="sort">Sort</button>
      <a id="title" href="/notebook/00000000-0000-4000-8000-000000000211">Fixture Notebook</a>
      <chat-panel><textarea></textarea></chat-panel></main>
      <div class="cdk-overlay-container"></div>''')
    page.evaluate('''({opened,ignore,delay})=>{
      window.escapes=0;window.selectionChanges=0;window.titleClicks=0;window.wrongClicks=0;
      window.ignoreEscape=ignore;window.unsafeEscapes=0;
      function open(){const root=document.querySelector('.cdk-overlay-container');if(root.children.length)return;
        root.innerHTML='<div class="cdk-overlay-backdrop cdk-overlay-transparent-backdrop cdk-overlay-backdrop-showing" style="position:fixed;inset:0;z-index:10;background:transparent"></div><div class="cdk-overlay-pane" style="position:fixed;right:0;top:0;z-index:11"><div role="menu" tabindex="-1" id="sort-menu"><button role="menuitemradio" aria-checked="true">Most recent</button><button role="menuitemradio" aria-checked="false">Title</button></div></div>';
        root.querySelectorAll('button').forEach(b=>b.onclick=()=>window.selectionChanges++);
        document.querySelector('#sort').setAttribute('aria-expanded','true');
        root.querySelector('[role=menu]').focus()}
      window.openSortFixture=open;document.querySelector('#sort').onclick=open;
      document.querySelector('#title').onclick=e=>{e.preventDefault();window.titleClicks++;history.pushState({},'',e.currentTarget.href)};
      document.addEventListener('keydown',e=>{if(e.key!=='Escape')return;
        window.escapes++;if(document.querySelector('#unknown'))window.unsafeEscapes++;
        if(window.ignoreEscape)return;
        document.querySelector('[role=menu]')?.remove();
        setTimeout(()=>document.querySelector('.cdk-overlay-backdrop')?.remove(),delay);
        document.querySelector('#sort').setAttribute('aria-expanded','false')});
      if(opened)open();
    }''', {'opened':opened,'ignore':ignore_escape,'delay':backdrop_delay})
