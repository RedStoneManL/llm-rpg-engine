"""Offline SVG rendering through system librsvg/cairo, no browser or network."""
import ctypes as c
from pathlib import Path
from bs4 import BeautifulSoup

root=Path(__file__).resolve().parents[1]
soup=BeautifulSoup((root/'report.html').read_text(),'html.parser')
style='''text{font-family:"WenQuanYi Zen Hei",sans-serif}.box{fill:#f6f8f5;stroke:#b5c7bd;stroke-width:1.3}.accent{fill:#e4f2e9;stroke:#308069;stroke-width:1.5}.danger{fill:#fff0e8;stroke:#cb8767}.edge{stroke:#788e91;stroke-width:1.8;fill:none}.darkbox{fill:#243e49}.label{fill:#63757c;font-size:12px}.title{fill:#223c45;font-size:16px;font-weight:bold}.sub{fill:#516974;font-size:12px}'''
r=c.CDLL('librsvg-2.so.2');cairo=c.CDLL('libcairo.so.2');g=c.CDLL('libgobject-2.0.so.0')
r.rsvg_handle_new_from_data.argtypes=[c.c_char_p,c.c_size_t,c.c_void_p];r.rsvg_handle_new_from_data.restype=c.c_void_p
r.rsvg_handle_render_cairo.argtypes=[c.c_void_p,c.c_void_p];r.rsvg_handle_render_cairo.restype=c.c_bool
cairo.cairo_image_surface_create.argtypes=[c.c_int,c.c_int,c.c_int];cairo.cairo_image_surface_create.restype=c.c_void_p
cairo.cairo_create.argtypes=[c.c_void_p];cairo.cairo_create.restype=c.c_void_p
cairo.cairo_set_source_rgb.argtypes=[c.c_void_p,c.c_double,c.c_double,c.c_double]
cairo.cairo_paint.argtypes=[c.c_void_p];cairo.cairo_scale.argtypes=[c.c_void_p,c.c_double,c.c_double]
cairo.cairo_surface_write_to_png.argtypes=[c.c_void_p,c.c_char_p]
cairo.cairo_destroy.argtypes=[c.c_void_p];cairo.cairo_surface_destroy.argtypes=[c.c_void_p];g.g_object_unref.argtypes=[c.c_void_p]
for name,svg in zip(['current-architecture','target-architecture'],soup.select('.diagram svg')):
    view=svg.get('viewbox',svg.get('viewBox')).split();w,h=int(view[2]),int(view[3])
    # HTML parsing lowercases SVG attributes; normalize viewBox for an SVG file.
    svg.attrs.pop('viewbox',None);svg['viewBox']=' '.join(view);svg['width']=str(w);svg['height']=str(h);svg['xmlns']='http://www.w3.org/2000/svg'
    for tag in svg.find_all('marker'):
        for attr in ['markerWidth','markerHeight','refX','refY']:
            if attr.lower() in tag.attrs:tag[attr]=tag.attrs.pop(attr.lower())
    st=soup.new_tag('style');st.string=style;svg.insert(0,st)
    raw=str(svg).encode();(root/'figures'/f'{name}.svg').write_bytes(raw)
    surface=cairo.cairo_image_surface_create(0,w*2,h*2);ctx=cairo.cairo_create(surface)
    cairo.cairo_set_source_rgb(ctx,1,1,1);cairo.cairo_paint(ctx);cairo.cairo_scale(ctx,2,2)
    handle=r.rsvg_handle_new_from_data(raw,len(raw),None)
    assert handle and r.rsvg_handle_render_cairo(handle,ctx)
    cairo.cairo_surface_write_to_png(surface,str(root/'figures'/f'{name}.png').encode())
    g.g_object_unref(handle);cairo.cairo_destroy(ctx);cairo.cairo_surface_destroy(surface)
print('Rendered two architecture diagrams with librsvg.')
