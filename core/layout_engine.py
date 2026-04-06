import math
from collections import defaultdict, deque

def calculate_radial_layout(nodes_info, edges_info, center_x=0, center_y=0):
    """
    Calculates a mathematical radial layout for a graph of nodes.
    Places the most connected node in the center and orbits the rest in concentric tiers.
    
    nodes_info: dict mapping node_id -> {'width': w, 'height': h}
    edges_info: list of tuples (source_id, target_id)
    returns: dict mapping node_id -> {'x': x, 'y': y}
    """
    if not nodes_info:
        return {}

    # 1. Build adjacency list and find degrees (connection counts)
    adj = defaultdict(list)
    degrees = defaultdict(int)
    for src, tgt in edges_info:
        if src in nodes_info and tgt in nodes_info:
            adj[src].append(tgt)
            adj[tgt].append(src)
            degrees[src] += 1
            degrees[tgt] += 1

    # 2. Find the center node (node with the highest number of connections)
    center_node = max(nodes_info.keys(), key=lambda n: degrees[n], default=list(nodes_info.keys())[0])

    # 3. Perform BFS to group nodes into distance "layers" from the center
    layers = defaultdict(list)
    visited = set([center_node])
    queue = deque([(center_node, 0)])

    while queue:
        curr, depth = queue.popleft()
        layers[depth].append(curr)
        for neighbor in adj[curr]:
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append((neighbor, depth + 1))

    # Handle disconnected components by placing them in the outermost layer
    unvisited = set(nodes_info.keys()) - visited
    if unvisited:
        max_depth = max(layers.keys()) if layers else 0
        layers[max_depth + 1] = list(unvisited)

    # 4. Calculate exact (x, y) coordinates
    positions = {}
    positions[center_node] = {'x': center_x, 'y': center_y}
    
    current_radius = 0
    layer_padding = 250  # Base distance extending outward between concentric rings

    for depth in sorted(layers.keys()):
        if depth == 0:
            continue
        
        nodes_in_layer = layers[depth]
        
        # Estimate required circumference to avoid overlap (diagonal + padding)
        total_arc_length = sum(math.hypot(nodes_info[n]['width'], nodes_info[n]['height']) + 150 for n in nodes_in_layer)
        
        # Calculate required radius to fit all nodes in this tier
        required_radius = total_arc_length / (2 * math.pi) if len(nodes_in_layer) > 1 else layer_padding
        current_radius = max(current_radius + layer_padding, required_radius)
        
        # Sort nodes in this layer based on the angle of their parent in the previous layer
        # This dramatically reduces the number of criss-crossing lines!
        def get_parent_angle(n):
            for neighbor in adj[n]:
                if neighbor in positions:
                    px, py = positions[neighbor]['x'], positions[neighbor]['y']
                    return math.atan2(py - center_y, px - center_x)
            return 0.0
        
        nodes_in_layer.sort(key=get_parent_angle)
        
        # Assign mathematically even positions along the circle
        angle_step = 2 * math.pi / len(nodes_in_layer)
        for i, n in enumerate(nodes_in_layer):
            angle = i * angle_step
            x = center_x + current_radius * math.cos(angle)
            y = center_y + current_radius * math.sin(angle)
            
            # Offset by half width/height so the actual center of the node is at (x,y)
            nx = x - nodes_info[n]['width'] / 2
            ny = y - nodes_info[n]['height'] / 2
            positions[n] = {'x': nx, 'y': ny}
            
    # Fix the center node's offset so it sits perfectly in the middle
    positions[center_node]['x'] -= nodes_info[center_node]['width'] / 2
    positions[center_node]['y'] -= nodes_info[center_node]['height'] / 2

    return positions