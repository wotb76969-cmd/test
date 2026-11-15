/* Read-only Supabase history; all rows filtered by RLS as well as user_id. */
const config = window.CHELSY_CONFIG || {};
const status = document.querySelector('#status');
const output = document.querySelector('#history');
const refresh = document.querySelector('#refresh');
const safe = v => typeof v === 'string' ? v : '';
async function load() {
  refresh.disabled = true;
  try {
    if (!config.supabaseUrl || !config.supabasePublishableKey) throw new Error('Supabase не настроен');
    const { createClient } = await import('https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2.49.4/+esm');
    const supabase = createClient(config.supabaseUrl, config.supabasePublishableKey);
    const { data: { user }, error: authError } = await supabase.auth.getUser();
    if (authError || !user) { status.textContent='Войдите в аккаунт Chelsy Group.'; return; }
    // 0.9.3: hide messages deleted in Chelsy AI (migration 003). Falls back to the
    // 0.9.2 query when the deleted_at column does not exist yet.
    let {data, error} = await supabase.from('chelsy_ai_messages')
      .select('role,content,created_at').eq('user_id',user.id).is('deleted_at', null)
      .order('created_at',{ascending:true}).limit(500);
    if (error) {
      ({data, error} = await supabase.from('chelsy_ai_messages')
        .select('role,content,created_at').eq('user_id',user.id)
        .order('created_at',{ascending:true}).limit(500));
    }
    if (error) throw error;
    output.replaceChildren();
    for (const message of (data || [])) {
      const box=document.createElement('div');
      box.className='bubble ' + (message.role==='user'?'user':'ai');
      const title=document.createElement('strong'); title.textContent=message.role==='user'?'Вы':'Chelsy AI';
      const body=document.createElement('div');body.textContent=safe(message.content);
      box.append(title,body); output.append(box);
    }
    status.textContent=`Сообщений: ${(data||[]).length}. Показаны последние синхронизированные данные.`;
  } catch (error) {status.textContent='Не удалось загрузить историю. Проверьте SQL-миграции и подключение к Supabase.';}
  finally {refresh.disabled=false;}
}
refresh.addEventListener('click', load);
load();
