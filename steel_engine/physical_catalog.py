"""Record the selected CFS product dimensions from the native section library."""
from . import cfs_sections


def selected_profile(model,name,*,id=None,hss_spec=None):
    if cfs_sections.is_hss(name):
        if hss_spec not in ('A500','A1085'):raise ValueError('Select the actual HSS specification')
        p=cfs_sections.parse_hss(name,hss_spec)
        return model.profile(id or name,'rhs',dict(h=p['depth'],b=p['flange'],t=p['t']),
            source=dict(kind='engine_section_catalog',name=name,specification=hss_spec,t_nom=p['t_nom']))
    p=cfs_sections.parse_designator(name)
    return model.profile(id or name,'z' if p['style']=='Z' else 'channel',dict(d=p['depth'],b=p['flange'],t=p['t'],lip=p['lip'],r=cfs_sections.RADIUS_BY_MIL[p['mil']]),
        lip_angle_deg=p.get('lip_angle_deg',90),
        source=dict(kind='engine_section_catalog',name=name,source='cfs_sections',
                    geometry_basis='Native selected SFIA design dimensions and per-mil inside bend radius'))
