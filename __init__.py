def classFactory(iface):
    from .layer_theme_manager import LayerThemeManagerPlugin
    return LayerThemeManagerPlugin(iface)
