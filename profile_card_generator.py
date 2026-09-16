import io, os, requests
from PIL import Image, ImageDraw, ImageFont

LOGO_PATH=os.path.join(os.path.dirname(__file__),"assets","titani_logo.jpg")

def _font(size,bold=False):
    paths=["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    for p in paths:
        try:return ImageFont.truetype(p,size)
        except:pass
    return ImageFont.load_default()

def _remote(url,size):
    if not url:return None
    try:
        r=requests.get(url,timeout=8); r.raise_for_status(); im=Image.open(io.BytesIO(r.content)).convert("RGBA"); im.thumbnail((size,size)); return im
    except:return None

def build_profile_card(player, orientation="vertical"):
    vertical=orientation!="horizontal"
    w,h=(900,1250) if vertical else (1400,760)
    im=Image.new("RGB",(w,h),(34,13,55)); d=ImageDraw.Draw(im)
    # layered community background
    for y in range(h):
        r=int(34+75*y/h); g=int(13+12*y/h); b=int(55+55*y/h); d.line((0,y,w,y),fill=(r,g,b))
    logo=None
    try: logo=Image.open(LOGO_PATH).convert("RGBA"); logo.thumbnail((125,125))
    except: pass
    if logo: im.paste(logo,(w-145,18),logo)
    icon=_remote(player.get("icon_url"),125)
    if icon: im.paste(icon,(25,25),icon)
    name=player.get("name") or "Giocatore"; tag=player.get("tag") or ""
    d.text((170,30),name,font=_font(42,True),fill="white",stroke_width=2,stroke_fill="black")
    d.text((170,82),tag,font=_font(24,True),fill=(235,235,235))
    club=player.get("club_name") or player.get("club") or "Senza club"
    if isinstance(club,dict): club=club.get("name") or "Senza club"
    d.text((25,170),str(club),font=_font(28,True),fill=(255,205,65))
    stats=[("Trofei",player.get("trophies")),("Brawler",player.get("brawlers")),("Livello",player.get("level")),("Prestigio",player.get("prestige")),("3v3",player.get("wins_3v3")),("Solo",player.get("wins_solo")),("Duo",player.get("wins_duo")),("Classificata",player.get("ranked_current")),("ELO",player.get("ranked_current_elo")),("Record carriera",player.get("ranked_career_peak") or player.get("ranked_peak"))]
    cols=2 if vertical else 5; boxw=(w-50-(cols-1)*12)//cols; boxh=92; y0=225
    for i,(label,val) in enumerate(stats):
        col=i%cols; row=i//cols; x=25+col*(boxw+12); y=y0+row*(boxh+12)
        d.rounded_rectangle((x,y,x+boxw,y+boxh),radius=16,fill=(24,18,48),outline=(144,88,220),width=3)
        d.text((x+16,y+10),label,font=_font(18,True),fill=(210,190,255)); txt="Non disponibile" if val is None else (f"{val:,}".replace(",",".") if isinstance(val,int) else str(val))
        d.text((x+16,y+42),txt,font=_font(24,True),fill="white")
    footer_y=y0+((len(stats)+cols-1)//cols)*(boxh+12)+12
    d.text((25,footer_y),"TITANI ABUSIVI • Sens GPT",font=_font(24,True),fill=(255,205,65))
    out=io.BytesIO(); im.save(out,"JPEG",quality=91); out.seek(0); out.name="profilo_titani.jpg"; return out
