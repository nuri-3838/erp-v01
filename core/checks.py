"""Django system check'leri — `manage.py check` (her deploy/reload öncesi) çalışır.

NOT: Eski `hesap_hiyerarsi_tutarli` (core.E001) check'i KALDIRILDI. O check, hesap
planındaki iki hiyerarşinin (kod metni vs ayrı `ust_hesap` FK) ayrışmasını yakalardı.
Artık `ust_hesap` FK yok; hiyerarşi TEK kaynaktan — `hesap_kodu` metninden — türetiliyor,
dolayısıyla ayrışma imkânsız ve check gereksiz. Yeni check gerekirse buraya eklenir.
"""
