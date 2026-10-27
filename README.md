## About Heat Exchanger Modeler:

Heat Exchanger Modeler is a simple application designed to help engineers, researchers, and students build 3D CAD models of tube bank heat exchangers with airfoil or circular/elliptical tubes, and to generate 2D CFD meshes of the tube bank.

## How it Works:

HX Modeler offers two primary functions:

1. **3D Tube Bank Modeling:**  
   Choose the tube shape: an airfoil (default coordinates or your own .xlsx/.csv file) or a circular/elliptical tube **(NEW)**. Set the tube dimensions, the arrangement and pitches, and the connections between tubes. HX Modeler builds the full heat exchanger as a single 3D solid, ready for CAD and further processing.

2. **2D Model and CFD Mesh**
   On the second page, HX Modeler takes the same tube bank and generates the 2D model and a CFD mesh with gmsh. You can mesh the full bank or a symmetric cell (middle row plus half rows above and below), with boundary layer and wake refinement.

## Key Features:

- **Coordinate File Support:** Compatible with .xlsx and .csv formats for custom airfoil profiles.
- **Circular and Elliptical Tubes:** Define the tube by its radii, with a symmetric option.
- **Staggered or Aligned Arrangements:** Free vertical and horizontal pitches, with automatic interference detection and the minimum pitch for each case.
- **Tube Connections:** U-bends or headers, with optional inlet and outlet fittings (both on the same side).
- **Fins:** Add plate fins to the tube bank, set by number of fins or by fin pitch.
- **Wall Thickness and Scale:** Hollow tubes with a defined wall thickness, plus an optional scale factor.
- **Smooth or Spline Profiles:** Smooth the contour or use a spline through all the points.
- **CFD Mesh Quality:** Mesh viewer and quality metrics (skewness, orthogonal quality, non-orthogonality, aspect ratio and more).
- **Mesh Export:** .msh, .su2, .unv and .cgns for 2D solvers, or an extruded mesh for OpenFOAM.
- **CAD Integration:** Export clean, ready-to-use models in .stl and .step formats.
- **User-Friendly Interface:** From tube profile to CAD model and CFD mesh with minimal input requirements.
