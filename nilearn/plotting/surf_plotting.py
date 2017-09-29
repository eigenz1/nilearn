"""
Functions for surface visualization.
Only matplotlib is required.
"""

# Import libraries
import nibabel
import numpy as np

# These will be removed (see comment on _points_in_unit_ball)
from sklearn.externals import joblib
from ..datasets.utils import _get_dataset_dir
import sklearn.cluster
# / These will be removed (see comment on _points_in_unit_ball)

import matplotlib.pyplot as plt

from mpl_toolkits.mplot3d import Axes3D
from nibabel import gifti

import nilearn.image
from .._utils.compat import _basestring
from .. import _utils
from .img_plotting import _get_colorbar_and_data_ranges


# Eventually, when this PR is ready, the sample locations inside the unit ball
# will be hardcoded. For now we just compute them in a simple way (we will find
# something better than the k-means hack) and cache the result.
memory = joblib.Memory(_get_dataset_dir('joblib'), verbose=False)


@memory.cache
def _points_in_unit_ball(n_points=20, dim=3):
    mc_cube = np.random.uniform(-1, 1, size=(5000, dim))
    mc_ball = mc_cube[(mc_cube**2).sum(axis=1) <= 1.]
    centroids, assignments, _ = sklearn.cluster.k_means(
        mc_ball, n_clusters=n_points)
    return centroids


# Eventually will be replaced by nilearn.image.resampling.coord_transform, but
# it only works for 3d images and 2d is useful for visu and debugging.
def _transform_coord(coords, affine):
    return affine.dot(np.vstack([coords.T, np.ones(coords.shape[0])]))[:-1].T


def _ball_sample_locations(nodes, affine, ball_radius=3, n_points=20):
    """Get n_points regularly spaced inside a ball."""
    if affine is None:
        affine = np.eye(nodes.shape[1] + 1)
    offsets_world_space = _points_in_unit_ball(
        dim=nodes.shape[1], n_points=n_points) * ball_radius
    # later:
    # offset_voxels = nilearn.image.resampling.coord_transform(
    #     *offset_voxels.T, np.linalg.inv(affine))
    mesh_voxel_space = _transform_coord(nodes, np.linalg.inv(affine))
    linear_map = np.eye(affine.shape[0])
    linear_map[:-1, :-1] = affine[:-1, :-1]
    offsets_voxel_space = _transform_coord(offsets_world_space,
                                           np.linalg.inv(linear_map))
    sample_locations_voxel_space = (mesh_voxel_space[:, np.newaxis, :] +
                                    offsets_voxel_space[np.newaxis, :])
    return sample_locations_voxel_space, offsets_voxel_space


def _ball_sampling(images, nodes, affine=None, ball_radius=3, n_points=20):
    """In each image, average samples drawn from a ball around each node."""
    images = np.asarray(images)
    nodes = np.asarray(nodes)
    sample_locations_voxel_space, offsets_voxel_space = _ball_sample_locations(
        nodes, affine, ball_radius=ball_radius, n_points=n_points)
    sample_indices = np.asarray(
        np.floor(sample_locations_voxel_space), dtype=int)
    pad_size = int(np.ceil(np.abs(offsets_voxel_space).max()))
    pad = [(0, 0)] + [(pad_size, pad_size)] * nodes.shape[1]
    sample_slices = [slice(None, None, None)] + np.s_[[
        ax + pad_size for ax in np.rollaxis(sample_indices, -1)
    ]]
    try:
        samples = np.pad(
            images, pad, mode='constant',
            constant_values=np.nan)[sample_slices]
        texture = np.nanmean(samples, axis=2)
        if not(np.isfinite(texture).all()):
            raise IndexError()
    except IndexError:
        raise ValueError('Some nodes of the mesh are outside the image')
    return texture, sample_indices, sample_locations_voxel_space


def niimg_to_surf_data(image, mesh_nodes, ball_radius=3.):
    """Extract surface data from a Nifti image.

    Parameters
    ----------

    image : niimg-like object, 3d or 4d.

    mesh_nodes : array-like,shape n_nodes * 3
        The coordinates of the nodes of the mesh.

    ball_radius : float, optional (default=3.).
        The radius (in mm) of the ball over which image intensities are
        averaged around each node.

    Returns
    -------
    texture: array-like, 1d or 2d.
        If image was a 3d image (e.g a stat map), a 1d vector is returned,
        containing one value for each mesh node.
        If image was a 4d image, a 2d array is returned, where each row
        corresponds to a mesh node.

    """
    image = nilearn.image.load_img(image)
    original_dimension = len(image.shape)
    image = _utils.check_niimg(image, atleast_4d=True)
    frames = np.rollaxis(image.get_data(), -1)
    texture, indices, locations = _ball_sampling(
        frames,
        mesh_nodes,
        _utils.compat.get_affine(image),
        ball_radius=ball_radius)
    if original_dimension == 3:
        texture = texture[0]
    return texture.T


# function to figure out datatype and load data
def load_surf_data(surf_data):
    """Loading data to be represented on a surface mesh.

    Parameters
    ----------
    surf_data : str or numpy.ndarray
        Either a file containing surface data (valid format are .gii,
        .mgz, .nii, .nii.gz, or Freesurfer specific files such as
        .thickness, .curv, .sulc, .annot, .label) or
        a Numpy array containing surface data.
    Returns
    -------
    data : numpy.ndarray
        An array containing surface data
    """
    # if the input is a filename, load it
    if isinstance(surf_data, _basestring):
        if (surf_data.endswith('nii') or surf_data.endswith('nii.gz') or
                surf_data.endswith('mgz')):
            data = np.squeeze(nibabel.load(surf_data).get_data())
        elif (surf_data.endswith('curv') or surf_data.endswith('sulc') or
                surf_data.endswith('thickness')):
            data = nibabel.freesurfer.io.read_morph_data(surf_data)
        elif surf_data.endswith('annot'):
            data = nibabel.freesurfer.io.read_annot(surf_data)[0]
        elif surf_data.endswith('label'):
            data = nibabel.freesurfer.io.read_label(surf_data)
        elif surf_data.endswith('gii'):
            gii = gifti.read(surf_data)
            try:
                data = np.zeros((len(gii.darrays[0].data), len(gii.darrays)))
                for arr in range(len(gii.darrays)):
                    data[:, arr] = gii.darrays[arr].data
                data = np.squeeze(data)
            except IndexError:
                raise ValueError('Gifti must contain at least one data array')
        else:
            raise ValueError(('The input type is not recognized. %r was given '
                              'while valid inputs are a Numpy array or one of '
                              'the following file formats: .gii, .mgz, .nii, '
                              '.nii.gz, Freesurfer specific files such as '
                              '.curv, .sulc, .thickness, .annot, '
                              '.label') % surf_data)
    # if the input is a numpy array
    elif isinstance(surf_data, np.ndarray):
        data = np.squeeze(surf_data)
    else:
        raise ValueError('The input type is not recognized. '
                         'Valid inputs are a Numpy array or one of the '
                         'following file formats: .gii, .mgz, .nii, .nii.gz, '
                         'Freesurfer specific files such as .curv,  .sulc, '
                         '.thickness, .annot, .label')
    return data


# function to figure out datatype and load data
def load_surf_mesh(surf_mesh):
    """Loading a surface mesh geometry

    Parameters
    ----------
    surf_mesh : str or numpy.ndarray
        Either a file containing surface mesh geometry (valid formats
        are .gii or Freesurfer specific files such as .orig, .pial,
        .sphere, .white, .inflated) or a list of two Numpy arrays,
        the first containing the x-y-z coordinates of the mesh
        vertices, the second containing the indices (into coords)
        of the mesh faces.

    Returns
    --------
    [coords, faces] : List of two numpy.ndarray
        The first containing the x-y-z coordinates of the mesh vertices,
        the second containing the indices (into coords) of the mesh faces.
    """
    # if input is a filename, try to load it
    if isinstance(surf_mesh, _basestring):
        if (surf_mesh.endswith('orig') or surf_mesh.endswith('pial') or
                surf_mesh.endswith('white') or surf_mesh.endswith('sphere') or
                surf_mesh.endswith('inflated')):
            coords, faces = nibabel.freesurfer.io.read_geometry(surf_mesh)
        elif surf_mesh.endswith('gii'):
            try:
                coords = gifti.read(surf_mesh).getArraysFromIntent(
                    nibabel.nifti1.intent_codes['NIFTI_INTENT_POINTSET'])[0].data
            except IndexError:
                raise ValueError('Gifti file needs to contain a data array '
                                 'with intent NIFTI_INTENT_POINTSET')
            try:
                faces = gifti.read(surf_mesh).getArraysFromIntent(
                    nibabel.nifti1.intent_codes['NIFTI_INTENT_TRIANGLE'])[0].data
            except IndexError:
                raise ValueError('Gifti file needs to contain a data array '
                                 'with intent NIFTI_INTENT_TRIANGLE')
        else:
            raise ValueError(('The input type is not recognized. %r was given '
                              'while valid inputs are one of the following '
                              'file formats: .gii, Freesurfer specific files '
                              'such as .orig, .pial, .sphere, .white, '
                              '.inflated or a list containing two Numpy '
                              'arrays [vertex coordinates, face indices]'
                              ) % surf_mesh)
    elif isinstance(surf_mesh, list):
        if len(surf_mesh) == 2:
            coords, faces = surf_mesh[0], surf_mesh[1]
        else:
            raise ValueError(('If a list is given as input, it must have '
                              'two elements, the first is a Numpy array '
                              'containing the x-y-z coordinates of the mesh '
                              'vertices, the second is a Numpy array '
                              'containing  the indices (into coords) of the '
                              'mesh faces. The input was a list with '
                              '%r elements.') % len(surf_mesh))
    else:
        raise ValueError('The input type is not recognized. '
                         'Valid inputs are one of the following file '
                         'formats: .gii, Freesurfer specific files such as '
                         '.orig, .pial, .sphere, .white, .inflated '
                         'or a list containing two Numpy arrays '
                         '[vertex coordinates, face indices]')

    return [coords, faces]


def plot_surf(surf_mesh, surf_map=None, bg_map=None,
              hemi='left', view='lateral', cmap=None,
              avg_method='mean', threshold=None, alpha='auto',
              bg_on_data=False, darkness=1, vmin=None, vmax=None,
              title=None, output_file=None, axes=None, figure=None, **kwargs):

    """ Plotting of surfaces with optional background and data

    .. versionadded:: 0.3

    Parameters
    ----------
    surf_mesh: str or list of two numpy.ndarray
        Surface mesh geometry, can be a file (valid formats are
        .gii or Freesurfer specific files such as .orig, .pial,
        .sphere, .white, .inflated) or
        a list of two Numpy arrays, the first containing the x-y-z coordinates
        of the mesh vertices, the second containing the indices
        (into coords) of the mesh faces.

    surf_map: str or numpy.ndarray, optional.
        Data to be displayed on the surface mesh. Can be a file (valid formats
        are .gii, .mgz, .nii, .nii.gz, or Freesurfer specific files such as
        .thickness, .curv, .sulc, .annot, .label) or
        a Numpy array

    bg_map: Surface data object (to be defined), optional,
        Background image to be plotted on the mesh underneath the
        surf_data in greyscale, most likely a sulcal depth map for
        realistic shading.

    hemi : {'left', 'right'}, default is 'left'
        Hemisphere to display.

    view: {'lateral', 'medial', 'dorsal', 'ventral'}, default is 'lateral'
        View of the surface that is rendered.

    cmap: matplotlib colormap, str or colormap object, default is None
        To use for plotting of the stat_map. Either a string
        which is a name of a matplotlib colormap, or a matplotlib
        colormap object. If None, matplolib default will be chosen

    avg_method: {'mean', 'median'}, default is 'mean'
        How to average vertex values to derive the face value, mean results
        in smooth, median in sharp boundaries.

    threshold : a number, None, or 'auto', default is None.
        If None is given, the image is not thresholded.
        If a number is given, it is used to threshold the image, values
        below the threshold (in absolute value) are plotted as transparent.

    alpha: float, alpha level of the mesh (not surf_data), default 'auto'
        If 'auto' is chosen, alpha will default to .5 when no bg_map
        is passed and to 1 if a bg_map is passed.

    bg_on_stat: bool, default is False
        If True, and a bg_map is specified, the surf_data data is multiplied
        by the background image, so that e.g. sulcal depth is visible beneath
        the surf_data.
        NOTE: that this non-uniformly changes the surf_data values according
        to e.g the sulcal depth.

    darkness: float, between 0 and 1, default is 1
        Specifying the darkness of the background image.
        1 indicates that the original values of the background are used.
        .5 indicates the background values are reduced by half before being
        applied.

    vmin, vmax: lower / upper bound to plot surf_data values
        If None , the values will be set to min/max of the data

    title : str, optional
        Figure title.

    output_file: str, or None, optional
        The name of an image file to export plot to. Valid extensions
        are .png, .pdf, .svg. If output_file is not None, the plot
        is saved to a file, and the display is closed.

    axes: instance of matplotlib axes, None, optional
        The axes instance to plot to. The projection must be '3d' (e.g.,
        `figure, axes = plt.subplots(subplot_kw={'projection': '3d'})`,
        where axes should be passed.).
        If None, a new axes is created.

    figure: instance of matplotlib figure, None, optional
        The figure instance to plot to. If None, a new figure is created.

    See Also
    --------
    nilearn.datasets.fetch_surf_fsaverage5 : For surface data object to be
        used as background map for this plotting function.

    nilearn.plotting.plot_surf_roi : For plotting statistical maps on brain
        surfaces.

    nilearn.plotting.plot_surf_stat_map for plotting statistical maps on
        brain surfaces.
    """

    # load mesh and derive axes limits
    mesh = load_surf_mesh(surf_mesh)
    coords, faces = mesh[0], mesh[1]
    limits = [coords.min(), coords.max()]

    # set view
    if hemi == 'right':
        if view == 'lateral':
            elev, azim = 0, 0
        elif view == 'medial':
            elev, azim = 0, 180
        elif view == 'dorsal':
            elev, azim = 90, 0
        elif view == 'ventral':
            elev, azim = 270, 0
        else:
            raise ValueError('view must be one of lateral, medial, '
                             'dorsal or ventral')
    elif hemi == 'left':
        if view == 'medial':
            elev, azim = 0, 0
        elif view == 'lateral':
            elev, azim = 0, 180
        elif view == 'dorsal':
            elev, azim = 90, 0
        elif view == 'ventral':
            elev, azim = 270, 0
        else:
            raise ValueError('view must be one of lateral, medial, '
                             'dorsal or ventral')
    else:
        raise ValueError('hemi must be one of right or left')

    # set alpha if in auto mode
    if alpha == 'auto':
        if bg_map is None:
            alpha = .5
        else:
            alpha = 1

    # if no cmap is given, set to matplotlib default
    if cmap is None:
        cmap = plt.cm.get_cmap(plt.rcParamsDefault['image.cmap'])
    else:
        # if cmap is given as string, translate to matplotlib cmap
        if isinstance(cmap, _basestring):
            cmap = plt.cm.get_cmap(cmap)

    # initiate figure and 3d axes
    if axes is None:
        if figure is None:
            figure = plt.figure()
        axes = figure.add_subplot(111, projection='3d',
                                  xlim=limits, ylim=limits)
    else:
        if figure is None:
            figure = axes.get_figure()
        axes.set_xlim(*limits)
        axes.set_ylim(*limits)
    axes.view_init(elev=elev, azim=azim)
    axes.set_axis_off()

    # plot mesh without data
    p3dcollec = axes.plot_trisurf(coords[:, 0], coords[:, 1], coords[:, 2],
                                  triangles=faces, linewidth=0.,
                                  antialiased=False,
                                  color='white')

    # If depth_map and/or surf_map are provided, map these onto the surface
    # set_facecolors function of Poly3DCollection is used as passing the
    # facecolors argument to plot_trisurf does not seem to work
    if bg_map is not None or surf_map is not None:

        face_colors = np.ones((faces.shape[0], 4))
        # face_colors[:, :3] = .5*face_colors[:, :3]  # why this?

        if bg_map is not None:
            bg_data = load_surf_data(bg_map)
            if bg_data.shape[0] != coords.shape[0]:
                raise ValueError('The bg_map does not have the same number '
                                 'of vertices as the mesh.')
            bg_faces = np.mean(bg_data[faces], axis=1)
            bg_faces = bg_faces - bg_faces.min()
            bg_faces = bg_faces / bg_faces.max()
            # control background darkness
            bg_faces *= darkness
            face_colors = plt.cm.gray_r(bg_faces)

        # modify alpha values of background
        face_colors[:, 3] = alpha * face_colors[:, 3]
        # should it be possible to modify alpha of surf data as well?

        if surf_map is not None:
            surf_map_data = load_surf_data(surf_map)
            if len(surf_map_data.shape) is not 1:
                raise ValueError('surf_map can only have one dimension but has'
                                 '%i dimensions' % len(surf_map_data.shape))
            if surf_map_data.shape[0] != coords.shape[0]:
                raise ValueError('The surf_map does not have the same number '
                                 'of vertices as the mesh.')

            # create face values from vertex values by selected avg methods
            if avg_method == 'mean':
                surf_map_faces = np.mean(surf_map_data[faces], axis=1)
            elif avg_method == 'median':
                surf_map_faces = np.median(surf_map_data[faces], axis=1)

            # if no vmin/vmax are passed figure them out from data
            if vmin is None:
                vmin = np.nanmin(surf_map_faces)
            if vmax is None:
                vmax = np.nanmax(surf_map_faces)

            # treshold if inidcated
            if threshold is None:
                kept_indices = np.where(surf_map_faces)[0]
            else:
                kept_indices = np.where(np.abs(surf_map_faces) >= threshold)[0]

            surf_map_faces = surf_map_faces - vmin
            surf_map_faces = surf_map_faces / (vmax - vmin)

            # multiply data with background if indicated
            if bg_on_data:
                face_colors[kept_indices] = cmap(surf_map_faces[kept_indices])\
                    * face_colors[kept_indices]
            else:
                face_colors[kept_indices] = cmap(surf_map_faces[kept_indices])

        p3dcollec.set_facecolors(face_colors)

    if title is not None:
        axes.set_title(title, position=(.5, .9))

    # save figure if output file is given
    if output_file is not None:
        figure.savefig(output_file)
        plt.close(figure)
    else:
        return figure


def plot_surf_stat_map(surf_mesh, stat_map, bg_map=None,
                       hemi='left', view='lateral', threshold=None,
                       alpha='auto', vmax=None, cmap='coolwarm',
                       symmetric_cbar="auto", bg_on_data=False, darkness=1,
                       title=None, output_file=None, axes=None, figure=None,
                       **kwargs):
    """ Plotting a stats map on a surface mesh with optional background

    .. versionadded:: 0.3

    Parameters
    ----------
    surf_mesh : str or list of two numpy.ndarray
        Surface mesh geometry, can be a file (valid formats are
        .gii or Freesurfer specific files such as .orig, .pial,
        .sphere, .white, .inflated) or
        a list of two Numpy arrays, the first containing the x-y-z
        coordinates of the mesh vertices, the second containing the
        indices (into coords) of the mesh faces

    stat_map : str or numpy.ndarray
        Statistical map to be displayed on the surface mesh, can
        be a file (valid formats are .gii, .mgz, .nii, .nii.gz, or
        Freesurfer specific files such as .thickness, .curv, .sulc, .annot,
        .label) or
        a Numpy array

    bg_map : Surface data object (to be defined), optional,
        Background image to be plotted on the mesh underneath the
        stat_map in greyscale, most likely a sulcal depth map for
        realistic shading.

    hemi : {'left', 'right'}, default is 'left'
        Hemispere to display.

    view : {'lateral', 'medial', 'dorsal', 'ventral'}, default 'lateral'
        View of the surface that is rendered.

    threshold : a number, None, or 'auto', default is None
        If None is given, the image is not thresholded.
        If a number is given, it is used to threshold the image,
        values below the threshold (in absolute value) are plotted
        as transparent.

    cmap : matplotlib colormap in str or colormap object, default 'coolwarm'
        To use for plotting of the stat_map. Either a string
        which is a name of a matplotlib colormap, or a matplotlib
        colormap object.

    alpha : float, alpha level of the mesh (not the stat_map), default 'auto'
        If 'auto' is chosen, alpha will default to .5 when no bg_map is
        passed and to 1 if a bg_map is passed.

    vmax : upper bound for plotting of stat_map values.

    symmetric_cbar : bool or 'auto', optional, default 'auto'
        Specifies whether the colorbar should range from -vmax to vmax
        or from vmin to vmax. Setting to 'auto' will select the latter
        if the range of the whole image is either positive or negative.
        Note: The colormap will always range from -vmax to vmax.

    bg_on_data : bool, default is False
        If True, and a bg_map is specified, the stat_map data is multiplied
        by the background image, so that e.g. sulcal depth is visible beneath
        the stat_map.
        NOTE: that this non-uniformly changes the stat_map values according
        to e.g the sulcal depth.

    darkness: float, between 0 and 1, default 1
        Specifying the darkness of the background image. 1 indicates that the
        original values of the background are used. .5 indicates the
        background values are reduced by half before being applied.

    title : str, optional
        Figure title.

    output_file: str, or None, optional
        The name of an image file to export plot to. Valid extensions
        are .png, .pdf, .svg. If output_file is not None, the plot
        is saved to a file, and the display is closed.

    axes: instance of matplotlib axes, None, optional
        The axes instance to plot to. The projection must be '3d' (e.g.,
        `figure, axes = plt.subplots(subplot_kw={'projection': '3d'})`,
        where axes should be passed.).
        If None, a new axes is created.

    figure: instance of matplotlib figure, None, optional
        The figure instance to plot to. If None, a new figure is created.

    See Also
    --------
    nilearn.datasets.fetch_surf_fsaverage5 : For surface data object to be
        used as background map for this plotting function.

    nilearn.plotting.plot_surf : For brain surface visualization.
    """

    # Call _get_colorbar_and_data_ranges to derive symmetric vmin, vmax
    # And colorbar limits depending on symmetric_cbar settings
    cbar_vmin, cbar_vmax, vmin, vmax = \
        _get_colorbar_and_data_ranges(stat_map, vmax,
                                      symmetric_cbar, kwargs)

    display = plot_surf(surf_mesh, surf_map=stat_map, bg_map=bg_map,
                        hemi=hemi, view=view, avg_method='mean',
                        threshold=threshold, cmap=cmap,
                        alpha=alpha, bg_on_data=bg_on_data, darkness=1,
                        vmax=vmax, title=title, output_file=output_file,
                        axes=axes, figure=figure, **kwargs)

    return display


def plot_surf_roi(surf_mesh, roi_map, bg_map=None,
                  hemi='left', view='lateral', alpha='auto',
                  vmin=None, vmax=None, cmap='coolwarm',
                  bg_on_data=False, darkness=1, title=None,
                  output_file=None, axes=None, figure=None, **kwargs):
    """ Plotting of surfaces with optional background and stats map

    .. versionadded:: 0.3

    Parameters
    ----------
    surf_mesh : str or list of two numpy.ndarray
        Surface mesh geometry, can be a file (valid formats are
        .gii or Freesurfer specific files such as .orig, .pial,
        .sphere, .white, .inflated) or
        a list of two Numpy arrays, the first containing the x-y-z
        coordinates of the mesh vertices, the second containing the indices
        (into coords) of the mesh faces

    roi_map : str or numpy.ndarray or list of numpy.ndarray
        ROI map to be displayed on the surface mesh, can be a file
        (valid formats are .gii, .mgz, .nii, .nii.gz, or Freesurfer specific
        files such as .annot or .label), or
        a Numpy array containing a value for each vertex, or
        a list of Numpy arrays, one array per ROI which contains indices
        of all vertices included in that ROI.

    hemi : {'left', 'right'}, default is 'left'
        Hemisphere to display.

    bg_map : Surface data object (to be defined), optional,
        Background image to be plotted on the mesh underneath the
        stat_map in greyscale, most likely a sulcal depth map for
        realistic shading.

    view : {'lateral', 'medial', 'dorsal', 'ventral'}, default 'lateral'
        View of the surface that is rendered.

    cmap : matplotlib colormap str or colormap object, default 'coolwarm'
        To use for plotting of the rois. Either a string which is a name
        of a matplotlib colormap, or a matplotlib colormap object.

    alpha : float, default is 'auto'
        Alpha level of the mesh (not the stat_map). If default,
        alpha will default to .5 when no bg_map is passed
        and to 1 if a bg_map is passed.

    bg_on_data : bool, default is False
        If True, and a bg_map is specified, the stat_map data is multiplied
        by the background image, so that e.g. sulcal depth is visible beneath
        the stat_map. Beware that this non-uniformly changes the stat_map
        values according to e.g the sulcal depth.

    darkness : float, between 0 and 1, default is 1
        Specifying the darkness of the background image. 1 indicates that the
        original values of the background are used. .5 indicates the background
        values are reduced by half before being applied.

    title : str, optional
        Figure title.

    output_file: str, or None, optional
        The name of an image file to export plot to. Valid extensions
        are .png, .pdf, .svg. If output_file is not None, the plot
        is saved to a file, and the display is closed.

    axes: Axes instance | None
        The axes instance to plot to. The projection must be '3d' (e.g.,
        `plt.subplots(subplot_kw={'projection': '3d'})`).
        If None, a new axes is created.

    figure: Figure instance | None
        The figure to plot to. If None, a new figure is created.

    See Also
    --------
    nilearn.datasets.fetch_surf_fsaverage5: For surface data object to be
        used as background map for this plotting function.

    nilearn.plotting.plot_surf: For brain surface visualization.
    """

    v, _ = load_surf_mesh(surf_mesh)

    # if roi_map is a list of arrays with indices for different rois
    if isinstance(roi_map, list):
        roi_list = roi_map[:]
        roi_map = np.zeros(v.shape[0])
        idx = 1
        for arr in roi_list:
            roi_map[arr] = idx
            idx += 1

    elif isinstance(roi_map, np.ndarray):
        # if roi_map is an array with values for all surface nodes
        roi_data = load_surf_data(roi_map)
        # or a single array with indices for a single roi
        if roi_data.shape[0] != v.shape[0]:
            roi_map = np.zeros(v.shape[0])
            roi_map[roi_data] = 1

    else:
        raise ValueError('Invalid input for roi_map. Input can be a file '
                         '(valid formats are .gii, .mgz, .nii, '
                         '.nii.gz, or Freesurfer specific files such as '
                         '.annot or .label), or a Numpy array containing a '
                         'value for each vertex, or a list of Numpy arrays, '
                         'one array per ROI which contains indices of all '
                         'vertices included in that ROI')

    display = plot_surf(surf_mesh, surf_map=roi_map, bg_map=bg_map,
                        hemi=hemi, view=view, avg_method='median',
                        cmap=cmap, alpha=alpha, bg_on_data=bg_on_data,
                        darkness=darkness, vmin=vmin, vmax=vmax,
                        title=title, output_file=output_file,
                        axes=axes, figure=figure, **kwargs)

    return display
