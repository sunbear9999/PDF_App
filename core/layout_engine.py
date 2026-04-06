import math
from collections import defaultdict, deque

def calculate_radial_layout(nodes_info, edges_info, center_x=0, center_y=0):
    """
    Calculates a mathematical radial layout for a graph of nodes.
    Places the most connected node in the center and orbits the rest in concentric tiers.
    """
    if not nodes_info:
        return {}

    try:
        adj = defaultdict(list)
        degrees = defaultdict(int)
        for src, tgt in edges_info:
            if src in nodes_info and tgt in nodes_info:
                adj[src].append(tgt)
                adj[tgt].append(src)
                degrees[src] += 1
                degrees[tgt] += 1

        center_node = max(nodes_info.keys(), key=lambda n: degrees[n], default=list(nodes_info.keys())[0])

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

        unvisited = set(nodes_info.keys()) - visited
        if unvisited:
            max_depth = max(layers.keys()) if layers else 0
            layers[max_depth + 1] = list(unvisited)

        positions = {}
        positions[center_node] = {'x': center_x, 'y': center_y}
        
        current_radius = 0
        layer_padding = 250 

        for depth in sorted(layers.keys()):
            if depth == 0:
                continue
            
            nodes_in_layer = layers[depth]
            
            total_arc_length = sum(math.hypot(nodes_info[n]['width'], nodes_info[n]['height']) + 150 for n in nodes_in_layer)
            
            required_radius = total_arc_length / (2 * math.pi) if len(nodes_in_layer) > 1 else layer_padding
            current_radius = max(current_radius + layer_padding, required_radius)
            
            def get_parent_angle(n):
                for neighbor in adj[n]:
                    if neighbor in positions:
                        px, py = positions[neighbor]['x'], positions[neighbor]['y']
                        return math.atan2(py - center_y, px - center_x)
                return 0.0
            
            nodes_in_layer.sort(key=get_parent_angle)
            
            angle_step = 2 * math.pi / len(nodes_in_layer)
            for i, n in enumerate(nodes_in_layer):
                angle = i * angle_step
                x = center_x + current_radius * math.cos(angle)
                y = center_y + current_radius * math.sin(angle)
                
                nx = x - nodes_info[n]['width'] / 2
                ny = y - nodes_info[n]['height'] / 2
                positions[n] = {'x': nx, 'y': ny}
                
        positions[center_node]['x'] -= nodes_info[center_node]['width'] / 2
        positions[center_node]['y'] -= nodes_info[center_node]['height'] / 2

        return positions
    except Exception as e:
        print(f"Error in mathematical layout engine: {e}")
        return {} # Fails gracefully