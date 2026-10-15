import xml.etree.ElementTree as ET
tree = ET.parse('C:/Users/nanda/Downloads/SURVIVOR_DETECTION/data/simulation_runs/retry29_short_probe_v8/generated_vehicle.sdf')
root = tree.getroot()
for coll in root.findall('.//collision'):
    name = coll.attrib.get('name')
    pose = coll.find('pose').text if coll.find('pose') is not None else ''
    geom = coll.find('geometry')
    for child in geom:
        print(f'Collision {name}: pose={pose}, shape={child.tag}')
        if child.tag == 'box':
            box = child.find('size')
            print(f'  size: {box.text if box is not None else "none"}')
