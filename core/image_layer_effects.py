"""Layer effects evaluated from document-space alpha, after masking."""
import numpy as np
from PIL import ImageColor


def has_layer_effects(layer):
    values = getattr(layer, 'effects', {})
    return any(getattr(layer, key, values.get(key, False)) for key in
               ('shadow', 'outer_glow', 'effect_stroke', 'color_overlay',
                'inner_shadow', 'inner_glow', 'bevel_emboss'))


def source_over(top, bottom):
    sa, da = top[:, :, 3:4], bottom[:, :, 3:4]
    alpha = sa + da*(1-sa)
    rgb = np.divide(top[:, :, :3]*sa + bottom[:, :, :3]*da*(1-sa), alpha,
                    out=np.zeros_like(top[:, :, :3]), where=alpha > 1e-8)
    return np.concatenate((rgb,alpha),axis=2)


def apply_layer_effects(pixels, layer):
    import cv2
    values = getattr(layer,'effects',{})
    def get(key, default):
        return getattr(layer,key,values.get(key,default))
    if not has_layer_effects(layer):
        if layer.opacity == 1:
            return pixels
        result = pixels.copy()
        result[:, :, 3] = np.rint(result[:, :, 3].astype(np.float32)*layer.opacity).astype(np.uint8)
        return result
    def color(value):
        return np.array(ImageColor.getrgb(value)[:3],np.float32)/255
    original = pixels.astype(np.float32)/255
    alpha = original[:, :, 3]
    h,w = alpha.shape
    def blur(mask, radius):
        return cv2.GaussianBlur(mask,(0,0),max(.1,float(radius)),borderType=cv2.BORDER_CONSTANT)
    def colored(mask, shade):
        result = np.empty_like(original)
        result[:, :, :3] = color(shade)
        result[:, :, 3] = np.clip(mask,0,1)
        return result
    back = np.zeros_like(original)
    if get('shadow',False):
        shifted = cv2.warpAffine(alpha,np.float32([[1,0,get('shadow_dx',4)],[0,1,get('shadow_dy',4)]]),(w,h))
        shadow = blur(shifted,get('shadow_blur',8)/2)*get('shadow_opacity',.5)
        back = source_over(colored(shadow,get('shadow_color','#000000')),back)
    if get('outer_glow',False):
        glow = blur(alpha,get('outer_glow_size',8))*get('outer_glow_opacity',.5)
        back = source_over(colored(glow,get('outer_glow_color','#00eaff')),back)
    if get('effect_stroke',False):
        radius = max(1,int(get('effect_stroke_width',3)))
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(radius*2+1,radius*2+1))
        stroke = np.maximum(0,cv2.dilate(alpha,kernel)-alpha)
        back = source_over(colored(stroke,get('effect_stroke_color','#ffffff')),back)
    rgb = original[:, :, :3].copy()
    if get('color_overlay',False):
        amount = get('color_overlay_opacity',1.)
        rgb = rgb*(1-amount)+color(get('color_overlay_color','#3d8ef8'))*amount
    if get('inner_shadow',False):
        shifted = cv2.warpAffine(alpha,np.float32([[1,0,get('inner_shadow_dx',3)],[0,1,get('inner_shadow_dy',3)]]),(w,h))
        edge = np.clip(1-blur(shifted,get('inner_shadow_blur',4)),0,1)*get('inner_shadow_opacity',.4)
        rgb = rgb*(1-edge[:, :, None])+color(get('inner_shadow_color','#000000'))*edge[:, :, None]
    if get('inner_glow',False):
        edge = np.clip(1-blur(alpha,get('inner_glow_size',8)),0,1)*get('inner_glow_opacity',.5)
        rgb = rgb*(1-edge[:, :, None])+color(get('inner_glow_color','#ffffff'))*edge[:, :, None]
    if get('bevel_emboss',False):
        softened = blur(alpha,get('bevel_size',3))
        gx,gy = cv2.Sobel(softened,cv2.CV_32F,1,0),cv2.Sobel(softened,cv2.CV_32F,0,1)
        light = np.clip((gx-gy)*get('bevel_depth',.5),-1,1)
        highlight,shade = np.maximum(light,0)[:, :, None],np.maximum(-light,0)[:, :, None]
        rgb = rgb*(1-highlight-shade)+color(get('bevel_highlight','#ffffff'))*highlight+color(get('bevel_shadow','#000000'))*shade
    original[:, :, :3] = rgb
    result = source_over(original,back)
    result[:, :, 3] *= get('opacity',1.)
    return np.rint(np.clip(result,0,1)*255).astype(np.uint8)
