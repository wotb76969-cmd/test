"""Configure public Supabase project settings on this PC. Never input secret keys."""
import json
import os
from pathlib import Path
from tkinter import Tk, Label, Entry, Button, StringVar, messagebox
import sys
from supabase_auth import config, AuthError

root=Tk()
root.title('Chelsy AI — Supabase connection')
root.geometry('620x280')
root.configure(padx=16,pady=12)
def load_site_public_config():
    # Only public Supabase URL and publishable key; never service-role.
    base = Path(getattr(sys, '_MEIPASS', Path(__file__).parent))
    source = base / 'site_supabase_public.json'
    try:
        data = json.loads(source.read_text(encoding='utf-8'))
        if (str(data.get('url', '')).startswith('https://') and
            str(data.get('publishable_key', '')).startswith(('sb_publishable_', 'eyJ'))):
            return data
    except (OSError, ValueError):
        pass
    return {}

public_defaults = load_site_public_config()
url=StringVar(value=public_defaults.get('url', ''))
key=StringVar(value=public_defaults.get('publishable_key', ''))
admin=StringVar()
for text,var in [('Supabase Project URL (https://...supabase.co)',url),('Publishable / anon key (NOT service_role)',key),('Admin Supabase user UUID (optional)',admin)]:
    Label(root,text=text).pack(anchor='w')
    Entry(root,textvariable=var,width=88).pack(fill='x',pady=(0,10))

def save():
    u=url.get().strip().rstrip('/')
    k=key.get().strip()
    a=admin.get().strip()
    if k.startswith('sb_secret_') or not k or 'service_role' in k.lower():
        return messagebox.showerror('Chelsy AI','Only publishable/anon key is allowed!')
    from urllib.parse import urlsplit
    p=urlsplit(u)
    if p.scheme!='https' or not p.hostname or p.path not in ('','/') or p.username or p.password or p.query or p.fragment:
        return messagebox.showerror('Chelsy AI','Enter an HTTPS Supabase project origin.')
    if a:
        import uuid
        try: a=str(uuid.UUID(a))
        except ValueError: return messagebox.showerror('Chelsy AI','Admin must be a UUID.')
    dest=Path(os.environ.get('LOCALAPPDATA') or Path.home())/'ChelsyAI'/'supabase.json'
    dest.parent.mkdir(parents=True,exist_ok=True)
    dest.write_text(json.dumps({'url':u,'publishable_key':k,'admin_user_id':a},indent=2),encoding='utf-8')
    messagebox.showinfo('Chelsy AI','Saved. Restart Chelsy AI and Host/Client.')

Button(root,text='Save connection',command=save).pack(pady=8)
root.mainloop()
