__all__ = ['SpeckPlot']

from typing import Union, Iterable, Optional, Tuple, Dict
from itertools import cycle
from functools import lru_cache
import logging

import numpy as np
import matplotlib.pyplot as plt
from matplotlib import figure
from matplotlib.axis import Axis
from PIL import Image

from speck.noise import Noise
from speck.colour import Colour
from speck.modifier import Modifier
from speck.types import XData, YData, NoiseData, ColourData

logger = logging.getLogger('speck')


class SpeckPlot:
    dpi = 100  # figure dpi used for plotting and saving

    def __init__(self, image: Image, upscale: int = 10, horizontal: bool = True):
        """
        Create a SpeckPlot from a PIL Image
        :param image: PIL image
        :param upscale: the pixel scaling factor, each input pixel maps to upscale output pixels
        :param horizontal: use horizontal lines to render the image
        """

        self.image = image
        self.scale = upscale
        self.horizontal = horizontal
        if self.horizontal:
            self.im = np.array(image.convert('L'))
        else:
            self.im = np.array(image.convert('L').rotate(-90, expand=1))

        self.h, self.w = self.im.shape
        figsize = self.w * upscale / self.dpi, self.h * upscale / self.dpi
        self.fig = plt.figure(figsize=figsize if self.horizontal else figsize[::-1])
        self.ax = self.fig.add_axes([0.0, 0.0, 1.0, 1.0], xticks=[], yticks=[])
        plt.close(self.fig)

        self.k = 10  # logistic growth rate on pixel boundaries
        self.inter = int(upscale) if upscale >= 10 else 10

        if max(self.im.shape) > 1000:
            logger.warning(
                'Very large image. Consider resizing with the resize argument. Calls to .draw() and .save() will be slow.'
            )

    @classmethod
    def from_path(
        cls,
        path: str,
        upscale: int = 10,
        resize: Optional[Union[int, Tuple[int, int]]] = None,
        horizontal: bool = True,
    ):
        """
        Create a SpeckPlot from an image path
        :param path: path to image file
        :param upscale: the pixel scaling factor, each input pixel maps to upscale output pixels
        :param resize: dimensions to resize to or a single value to set the long edge to and keep the input aspect ratio
        :param horizontal: use horizontal lines to render the image
        """

        image = Image.open(path)
        return cls(cls._resize_image(image, resize), upscale, horizontal)

    @classmethod
    def from_url(
        cls,
        url: str,
        upscale: int = 10,
        resize: Optional[Union[int, Tuple[int, int]]] = None,
        horizontal: bool = True,
    ):
        """
        Create SpeckPlot from image URL
        :param url: url string
        :param upscale: the pixel scaling factor, each input pixel maps to upscale output pixels
        :param resize: dimensions to resize to or a single value to set the long edge to and keep the input aspect ratio
        :param horizontal: use horizontal lines to render the image
        """

        import requests
        from io import BytesIO

        image = Image.open(BytesIO(requests.get(url).content))
        return cls(cls._resize_image(image, resize), upscale, horizontal)

    @staticmethod
    def _resize_image(
        image: Image, resize: Optional[Union[int, Tuple[int, int]]]
    ) -> Image:
        if resize is not None:
            if isinstance(resize, int):
                factor = resize / max(image.size)
                resize = round(image.size[0] * factor), round(image.size[1] * factor)
            image = image.resize(resize)
        return image

    def __repr__(self):
        d = [f'{k}={v}' for k, v in self.__dict__.items() if not k.startswith('_')]
        return f'{self.__class__.__name__}({", ".join(d)})'

    def _clear_ax(self, background: Union[str, Tuple[float, ...]]) -> None:
        self.ax.clear()
        self.ax.set_facecolor(background)
        if self.horizontal:
            self.ax.invert_yaxis()
            self.ax.set_ylim(self.h, 0)
            self.ax.set_xlim(0, self.w)
        else:
            self.ax.set_ylim(0, self.w)
            self.ax.set_xlim(0, self.h)
        self.ax.spines['left'].set_visible(False)
        self.ax.spines['right'].set_visible(False)
        self.ax.spines['top'].set_visible(False)
        self.ax.spines['bottom'].set_visible(False)
        self.ax.set_xticks([])
        self.ax.set_yticks([])

    def cache_clear(self, parameter: Optional[str] = None) -> None:
        if parameter is not None:
            getattr(self, parameter).cache_clear()
        else:
            self._x.cache_clear()
            self._y.cache_clear()
            self._noise.cache_clear()

    def cache_info(self) -> Dict[str, Tuple[int, ...]]:
        return {
            'x': self._x.cache_info(),
            'y': self._y.cache_info(),
            'noise': self._noise.cache_info(),
        }

    def set_k(self, k: int) -> None:
        self.k = k
        self.cache_clear()

    @lru_cache()
    def _x(self) -> XData:
        return np.linspace(0, self.w, self.w * self.inter)

    @lru_cache()
    def _y(
        self,
        weights: Tuple[float, float],
        weight_clipping: Tuple[float, float],
        skip: int,
    ) -> YData:
        y_min = weights[0] / 2 + 0.5
        y_max = weights[1] / 2 + 0.5
        clip_min = (1 - weight_clipping[1]) * 255.0
        clip_max = (1 - weight_clipping[0]) * 255.0

        def repeat_head_tail(arr: np.ndarray, n: int) -> np.ndarray:
            repeated = np.insert(
                np.insert(arr, 0, np.ones(n // 2) * arr[0]),
                -1,
                np.ones(n // 2) * arr[-1],
            )

            if n % 2:
                # odd, so append the last point again
                repeated = np.append(repeated, repeated[-1])

            return repeated

        y = []
        for i, line in enumerate(self.im):
            if i % (skip + 1):
                continue

            # apply clipping
            line = (
                (line.clip(clip_min, clip_max) - clip_min) * 255 / (clip_max - clip_min)
            )

            y_offset = np.repeat(y_max - line[:-1] * (y_max - y_min) / 255, self.inter)
            L = (
                np.repeat(y_max - line[1:] * (y_max - y_min) / 255, self.inter)
                - y_offset
            )

            x0 = np.repeat(np.arange(1, self.w), self.inter)

            y_offset = repeat_head_tail(y_offset, self.inter)
            L = repeat_head_tail(L, self.inter)
            x0 = repeat_head_tail(x0, self.inter)

            y_top: np.ndarray = i + (
                L / (1 + np.exp(-self.k * (self._x() - x0)))
            ) + y_offset
            y_bot: np.ndarray = 2 * i + 1 - y_top

            y.append((y_top, y_bot))

        return y

    @lru_cache()
    def _noise(self, noise: Optional[Noise]) -> NoiseData:
        if noise is not None:
            return noise(self.h, self.w * self.inter)
        else:
            return [(0, 0) for _ in range(self.h)]

    def _colour(self, colour: Union[str, Iterable, Colour]) -> ColourData:
        if isinstance(colour, str):
            return [colour]
        if isinstance(colour, Iterable):
            return colour
        if isinstance(colour, Colour):
            return colour(self.h)

    def draw(
        self,
        weights: Tuple[float, float] = (0, 1),
        weight_clipping: Tuple[float, float] = (0, 1),
        noise: Optional[Noise] = None,
        colour: Union[str, Iterable, Colour] = 'black',
        skip: int = 0,
        background: Union[str, Tuple[float, ...]] = 'white',
        modifiers: Optional[Iterable[Modifier]] = None,
        seed: Optional[int] = None,
        ax: Optional[Axis] = None,
    ) -> figure:
        """
        Render the input image to produce a matplotlib figure

        :param weights: min and max line widths
                eg. weights = (0.2, 0.9) =
                    0.2 units of line weight mapped from <= min darkness offset
                    0.9 units of line weight mapped from >= max darkness offset
        :param weight_clipping: proportion of greys that map to min and max weights.
                eg. weight_clipping = (0.1, 0.8) =
                    <=10% grey maps to min weight
                    >=80% grey maps to max weight
        :param noise: Noise object that is called and added onto thickness values
        :param colour: colour or list of colours or Colour object that is called and applied to lines
        :param skip: number of lines of pixels to skip for each plotted line
        :param background: background colour of output plot
        :param modifiers: list of Modifier objects that are iteratively applied to the output x, y, noise and colour data
        :param seed: random seed value
        :param ax: optional Axis object to plot on to
        :return: matplotlib figure object containing the plot
        """

        if seed is not None:
            np.random.seed(seed)

        x = self._x()
        y = self._y(weights, weight_clipping, skip)
        n = self._noise(noise)
        c = self._colour(colour)

        # run modifiers if necessary
        if modifiers is not None:
            for m in modifiers:
                x, y, n, c = m(x, y, n, c)

        # create plot elements
        if ax is not None:
            self.ax = ax
        self._clear_ax(background)
        for y_, n_, c_ in zip(y, cycle(n), cycle(c)):
            y_top = y_[0] + n_[0]
            y_bot = y_[1] + n_[1]

            if self.horizontal:
                self.ax.fill_between(x, y_top, y_bot, color=c_, lw=0)
            else:
                self.ax.fill_betweenx(x, y_top, y_bot, color=c_, lw=0)

        return self.fig

    def save(self, path: str, transparent: bool = False) -> None:
        """
        Save rendered figure to disk. Call this after the draw method
        :param path: path to save location
        :param transparent: whether to save with a transparent background (assuming .png extension)
        """

        self.fig.savefig(
            path,
            dpi=self.dpi,
            bbox_inches='tight',
            pad_inches=0,
            transparent=transparent,
        )
