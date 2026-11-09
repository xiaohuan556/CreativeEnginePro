"""Editable photo development and optical effects, evaluated in floating point."""
import numpy as np

EXTRA_ADJUSTMENTS = {
 'camera_raw': ('照片显影', [('exposure','曝光 EV ×100',-500,500,0), ('contrast','对比度',-100,100,0),
    ('highlights','高光',-100,100,0), ('shadows','阴影',-100,100,0), ('whites','白色',-100,100,0),
    ('blacks','黑色',-100,100,0), ('temperature','色温',-100,100,0), ('tint','色调',-100,100,0),
    ('vibrance','自然饱和度',-100,100,0), ('saturation','饱和度',-100,100,0),
    ('texture','纹理',-100,100,0), ('clarity','清晰度',-100,100,0), ('dehaze','去雾',-100,100,0)]),
 'gradient_map': ('渐变映射', [('hue_start','暗部色相',0,359,220), ('hue_end','亮部色相',0,359,35),
    ('saturation','色彩饱和度',0,100,60)]),
 'grain': ('胶片颗粒', [('amount','颗粒强度',0,100,15), ('size','颗粒尺寸',1,20,1)]),
 'bloom': ('高光柔光', [('threshold','高光阈值',0,100,70), ('amount','强度',0,100,30), ('radius','柔光半径',1,100,15)]),
 'dither': ('有序抖动', [('levels','色阶数',2,32,4)]),
 'tonal_contrast': ('局部色调对比', [('amount','强度',-100,100,20), ('radius','局部范围',1,100,15)]),
 'progressive_blur': ('渐进模糊', [('radius','模糊半径',1,100,15), ('center','清晰位置 %',0,100,50), ('width','清晰带宽 %',0,100,25)]),
 'lens_correction': ('镜头校正', [('distortion','桶形 / 枕形',-100,100,0), ('vignette','暗角补偿',-100,100,0)]),
 'lens_flare': ('光学耀斑', [('x','光源 X %',0,100,70), ('y','光源 Y %',0,100,25), ('amount','强度',0,100,35),
    ('length','光条长度 %',1,100,50), ('warmth','冷暖色',-100,100,0)]),
 'starburst': ('星芒', [('x','光源 X %',0,100,70), ('y','光源 Y %',0,100,25), ('amount','强度',0,100,35),
    ('points','光芒数量',2,16,6), ('angle','旋转角度',-180,180,0), ('length','长度 %',1,100,35)]),
 'mist': ('柔雾', [('amount','强度',0,100,25), ('radius','扩散半径',1,100,20)]),
 'prism': ('棱镜重影', [('copies','重影数量',1,8,3), ('offset','偏移像素',1,100,12), ('amount','强度',0,100,25), ('angle','方向',-180,180,0)]),
 'bokeh': ('散景柔化', [('radius','散景半径',1,60,8), ('amount','混合强度',0,100,60)]),
 'volumetric_light': ('体积光', [('x','光源 X %',0,100,50), ('y','光源 Y %',0,100,15), ('threshold','亮部阈值',0,100,65),
    ('amount','光束强度',0,100,35), ('density','光束延伸',1,100,50)]),
 'rim_light': ('边缘光', [('amount','强度',0,100,35), ('radius','光晕范围',1,60,8), ('warmth','冷暖色',-100,100,20)]),
}


def apply_development(rgb, kind, params, alpha=None):
    import cv2
    source = rgb.astype(np.float32)/255
    h, w = source.shape[:2]
    def blur(image, radius):
        return cv2.GaussianBlur(image, (0,0), max(.1,float(radius)))
    def color(warmth=0):
        t = warmth/100
        return np.array([1.,1.-abs(t)*.15,1.-max(t,0)*.5]) if t >= 0 else np.array([1.+t*.5, .9,1.])
    if kind == 'camera_raw':
        result = source * 2**(params.get('exposure',0)/100)
        lum = np.clip(source @ [.2126,.7152,.0722],0,1)[:, :, None]
        result += ((1-lum)**2*params.get('shadows',0) + lum**2*params.get('highlights',0))/300
        result += ((1-lum)**4*params.get('blacks',0) + lum**4*params.get('whites',0))/400
        result = (result-.5)*(1+params.get('contrast',0)/100)+.5
        temp, tint = params.get('temperature',0)/100, params.get('tint',0)/100
        result += np.array([temp*.12+tint*.05, -tint*.08, -temp*.12+tint*.05])
        gray = result @ [.2126,.7152,.0722]
        spread = np.max(result,axis=2)-np.min(result,axis=2)
        saturation = 1 + params.get('saturation',0)/100 + params.get('vibrance',0)/100*(1-np.clip(spread,0,1))
        result = gray[:, :, None]+(result-gray[:, :, None])*saturation[:, :, None]
        for key, radius, strength in (('texture',1.2,.5),('clarity',8,.7)):
            amount = params.get(key,0)/100
            if amount:
                result += (result-blur(result,radius))*amount*strength
        haze = params.get('dehaze',0)/100
        result = (result-.5)*(1+haze*.6)+.5
    elif kind == 'gradient_map':
        hsv = np.float32([[[params.get('hue_start',220),params.get('saturation',60)/100,.15],
                          [params.get('hue_end',35),params.get('saturation',60)/100,1.]]])
        colors = cv2.cvtColor(hsv,cv2.COLOR_HSV2RGB)[0]
        lum = np.clip(source @ [.2126,.7152,.0722],0,1)[:, :, None]
        result = colors[0]*(1-lum)+colors[1]*lum
    elif kind == 'grain':
        size = max(1,int(params.get('size',1)))
        grain = np.random.default_rng(0).normal(0,params.get('amount',15)/400,(max(1,h//size),max(1,w//size))).astype(np.float32)
        grain = cv2.resize(grain,(w,h),interpolation=cv2.INTER_LINEAR)
        result = source+grain[:, :, None]
    elif kind in ('bloom','mist'):
        threshold = params.get('threshold',0)/100 if kind == 'bloom' else 0
        light = np.clip((source-threshold)/max(.01,1-threshold),0,1)
        glow = blur(light,params.get('radius',15))*params.get('amount',30)/100
        result = 1-(1-source)*(1-glow)
    elif kind == 'dither':
        matrix = np.array([[0,8,2,10],[12,4,14,6],[3,11,1,9],[15,7,13,5]],np.float32)/16-.5
        tiled = np.tile(matrix,((h+3)//4,(w+3)//4))[:h,:w,None]
        levels = max(2,params.get('levels',4))-1
        result = np.floor(source*levels+tiled+.5)/levels
    elif kind == 'tonal_contrast':
        result = source+(source-blur(source,params.get('radius',15)))*params.get('amount',20)/100
    elif kind == 'progressive_blur':
        y = np.linspace(0,1,h)[:, None, None]
        amount = np.clip((abs(y-params.get('center',50)/100)-params.get('width',25)/200)*2,0,1)
        result = source*(1-amount)+blur(source,params.get('radius',15))*amount
    elif kind == 'lens_correction':
        yy,xx = np.mgrid[:h,:w].astype(np.float32)
        nx,ny = (xx-w/2)/max(w/2,1),(yy-h/2)/max(h/2,1)
        radius = nx*nx+ny*ny
        distortion = 1+params.get('distortion',0)/500*radius
        result = cv2.remap(source,(nx*distortion*w/2+w/2).astype(np.float32),
                            (ny*distortion*h/2+h/2).astype(np.float32),cv2.INTER_LINEAR,borderMode=cv2.BORDER_REFLECT_101)
        result *= 1+radius[:, :, None]*params.get('vignette',0)/200
    elif kind in ('lens_flare','starburst'):
        yy,xx = np.mgrid[:h,:w].astype(np.float32)
        dx,dy = xx-w*params.get('x',70)/100,yy-h*params.get('y',25)/100
        radius = max(w,h)*params.get('length',50)/100
        distance = np.hypot(dx,dy)
        if kind == 'lens_flare':
            glow = np.exp(-abs(dx)/max(radius,.1)-dy*dy/max(1,radius*.025)**2)
        else:
            theta = np.arctan2(dy,dx)-np.deg2rad(params.get('angle',0))
            rays = np.maximum(0,np.cos(theta*params.get('points',6)))**40
            glow = (rays*np.exp(-distance/max(radius,.1))+np.exp(-distance/max(radius*.08,1)))
        result = source+glow[:, :, None]*params.get('amount',35)/100*color(params.get('warmth',0))
    elif kind == 'prism':
        result = source.copy()
        angle = np.deg2rad(params.get('angle',0))
        copies = max(1,params.get('copies',3))
        amount = params.get('amount',25)/100/copies
        for index in range(1,copies+1):
            shift = params.get('offset',12)*index
            shifted = cv2.warpAffine(source,np.float32([[1,0,np.cos(angle)*shift],[0,1,np.sin(angle)*shift]]),(w,h),borderMode=cv2.BORDER_REFLECT_101)
            result += (shifted-source)*amount
    elif kind == 'bokeh':
        radius = max(1,params.get('radius',8))
        yy,xx = np.ogrid[-radius:radius+1,-radius:radius+1]
        kernel = (xx*xx+yy*yy <= radius*radius).astype(np.float32)
        kernel /= kernel.sum()
        blurred = cv2.filter2D(source,-1,kernel)
        amount = params.get('amount',60)/100
        result = source*(1-amount)+blurred*amount
    elif kind == 'volumetric_light':
        lum = source.max(axis=2)
        threshold = params.get('threshold',65)/100
        seed = np.clip((lum-threshold)/max(.01,1-threshold),0,1)
        yy,xx = np.mgrid[:h,:w].astype(np.float32)
        cx,cy = w*params.get('x',50)/100,h*params.get('y',15)/100
        light = np.zeros((h,w),np.float32)
        for step in range(1,17):
            t = step/16*params.get('density',50)/100
            light += cv2.remap(seed,xx+(cx-xx)*t,yy+(cy-yy)*t,cv2.INTER_LINEAR,borderMode=cv2.BORDER_REFLECT_101)/16
        result = source+light[:, :, None]*params.get('amount',35)/100
    elif kind == 'rim_light':
        edge_source = alpha.astype(np.float32)/255 if alpha is not None and np.any(alpha < 255) else source @ [.2126,.7152,.0722]
        edge_source = edge_source.astype(np.float32)
        gx,gy = cv2.Sobel(edge_source,cv2.CV_32F,1,0),cv2.Sobel(edge_source,cv2.CV_32F,0,1)
        edge = blur(np.clip(np.hypot(gx,gy),0,1),params.get('radius',8))
        result = source+edge[:, :, None]*params.get('amount',35)/100*color(params.get('warmth',20))
    else:
        result = source
    return np.clip(result,0,1)*255
