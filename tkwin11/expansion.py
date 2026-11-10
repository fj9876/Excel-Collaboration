"""Shared interruptible clipped-height expansion for both public containers."""
from .motion import Tween


class Expansion:
    def __init__(self,owner,button,show,hide):
        self.owner=owner;self.button=button;self.show=show;self.hide=hide
        self.animator=Tween(owner);self.height=0.;self.target=0;self.job=owner.after_idle(self.initialize)
        self.measuring=False
        self.arrow_animator=Tween(owner);self.angle=float(owner.opened)
        owner.body.bind('<Configure>',self.resized,add='+')
        owner.bind('<Destroy>',self.destroy,add='+')
        button.bind('<Return>',lambda e:self.toggle())
        button.bind('<Right>',lambda e:self.set_open(True))
        button.bind('<Left>',lambda e:self.set_open(False))
    def initialize(self):
        self.job=None
        if self.owner.opened:
            self.show();self.owner.update_idletasks()
            self.target=self.measure();self.paint(self.target)
        else:self.hide()
        self.owner.arrow()
    def measure(self):
        if not any(child.winfo_manager() for child in self.owner.body.winfo_children()):return 0
        return self.owner.body.winfo_reqheight()
    def paint(self,value):
        self.height=value;self.owner._height=value
        self.owner.viewport.configure(height=max(1,round(value)))
    def arrow(self):
        self.arrow_animator.start(self.angle,float(self.owner.opened),self.paint_arrow,duration=250)
    def paint_arrow(self,value):
        import math
        from .core import PALETTE
        from .render import SmoothImage
        self.angle=value
        root=self.owner._root();resources=getattr(root,'_dpi_resources',None)
        scale=resources.base_dpi/96 if resources else self.owner.winfo_fpixels('1i')/96
        size=round(16*scale);step=round(value*30)
        color=PALETTE['disabled'] if self.button.instate(['disabled']) else PALETTE['muted']
        cache=getattr(root,'_expander_arrows',None)
        if cache is None:cache=root._expander_arrows={}
        key=(size,step,color)
        if key not in cache:
            def factory(ratio=1,fg=color):
                extent=max(1,round(size*ratio));unit=scale*ratio;theta=math.pi*step/30;points=[]
                for x,y in [(-3,-1.5),(0,1.5),(3,-1.5)]:
                    points.extend((extent/2+unit*(x*math.cos(theta)-y*math.sin(theta)),extent/2+unit*(x*math.sin(theta)+y*math.cos(theta))))
                with SmoothImage(root,extent,extent) as surface:
                    surface.line(points,fg,max(1,unit));return surface.photo()
            factory.cache_key=('expander-arrow',size,step,color)
            def disabled(ratio):return factory(ratio,PALETTE['disabled'])
            disabled.cache_key=('expander-arrow',size,step,PALETTE['disabled'])
            factory.disabled_factory=disabled
            cache[key]=factory()
            if resources:resources.image_factories[str(cache[key])]=factory
        self.button.configure(image=cache[key])
    def set_open(self,value):
        if not self.owner.instate(['disabled']) and not self.button.instate(['disabled']):
            self.owner.opened=value;self.sync()
        return 'break'
    def toggle(self):return self.set_open(not self.owner.opened)
    def sync(self):
        o=self.owner;o.arrow()
        if not o.opened:
            focus=o.focus_get()
            if focus is not None and str(focus).startswith(str(o.body)+'.'):self.button.focus_set()
        self.show();self.measuring=True
        try:o.update_idletasks()
        finally:self.measuring=False
        self.target=self.measure() if o.opened else 0
        def done():
            if not o.opened:self.hide()
            elif abs(self.height-self.measure())>1:self.sync()
        self.animator.start(self.height,self.target,self.paint,duration=250,done=done)
    def resized(self,event):
        if self.owner.opened and abs(self.target-self.measure())>1 and self.job is None:
            self.job=self.owner.after_idle(self.retarget)
    def retarget(self):
        self.job=None
        # Inside sync()'s layout pass: it measures and animates right after.
        if not self.owner.opened or self.measuring:return
        if self.animator.job is None:
            # Content resized while open (late layout, wrapping): follow it at
            # once. A 250 ms tween here relayouts the whole page every frame
            # and, rounding, jitters the card by a pixel.
            self.target=self.measure();self.paint(self.target)
        else:self.sync()
    def destroy(self,event):
        if event.widget is self.owner and self.job is not None:self.owner.after_cancel(self.job);self.job=None
