import math
import random
from collections import defaultdict

def calculate_force_directed_layout(nodes_info, edges_info, center_x=0, center_y=0, iterations=150, similarity_matrix=None):
    """
    Physics-based force-directed layout.
    Uses similarity_matrix to weakly attract semantically similar nodes and reduce their repulsion.
    """
    if not nodes_info:
        return {}
    if len(nodes_info) == 1:
        n = list(nodes_info.keys())[0]
        return {n: {'x': center_x - nodes_info[n]['width']/2, 'y': center_y - nodes_info[n]['height']/2}}

    if similarity_matrix is None:
        similarity_matrix = defaultdict(lambda: defaultdict(float))

    try:
        pos = {}
        nodes = list(nodes_info.keys())
        
        avg_w = sum(n['width'] for n in nodes_info.values()) / len(nodes_info)
        avg_h = sum(n['height'] for n in nodes_info.values()) / len(nodes_info)
        k = max(avg_w, avg_h) * 1.5 
        
        radius = 50 * math.sqrt(len(nodes))
        for n in nodes:
            angle = random.uniform(0, 2 * math.pi)
            r = random.uniform(0, radius)
            pos[n] = [center_x + r * math.cos(angle), center_y + r * math.sin(angle)]

        adj = defaultdict(list)
        for u, v in edges_info:
            if u in nodes_info and v in nodes_info:
                adj[u].append(v)
                adj[v].append(u)

        t = k * 2.0 

        for i in range(iterations):
            disp = {n: [0.0, 0.0] for n in nodes}

            # A. Calculate Repulsive & Semantic Forces
            for idx_u in range(len(nodes)):
                for idx_v in range(idx_u + 1, len(nodes)):
                    u, v = nodes[idx_u], nodes[idx_v]
                    dx = pos[u][0] - pos[v][0]
                    dy = pos[u][1] - pos[v][1]
                    
                    if dx == 0 and dy == 0:
                        dx, dy = random.uniform(-1, 1), random.uniform(-1, 1)

                    dist = math.hypot(dx, dy)
                    dist = max(dist, 0.01)

                    sim = similarity_matrix.get(u, {}).get(v, 0.0)
                    
                    # 1. Calculate Base Repulsion (Diminished by semantic similarity)
                    repulse = (k ** 2) / dist
                    repulsion_modifier = 1.0 - (sim * 0.8) # Up to 80% less repulsion for identical nodes
                    repulse *= repulsion_modifier
                    
                    # Hard collision override (prevents physical overlap)
                    pad = 50
                    min_x = (nodes_info[u]['width'] + nodes_info[v]['width']) / 2 + pad
                    min_y = (nodes_info[u]['height'] + nodes_info[v]['height']) / 2 + pad
                    if abs(dx) < min_x and abs(dy) < min_y:
                        repulse *= 5.0 

                    disp[u][0] += (dx / dist) * repulse
                    disp[u][1] += (dy / dist) * repulse
                    disp[v][0] -= (dx / dist) * repulse
                    disp[v][1] -= (dy / dist) * repulse

                    # 2. Semantic Attraction (Invisible Springs)
                    if sim > 0.6:
                        semantic_attract = ((dist ** 2) / k) * (sim * 0.1) 
                        disp[u][0] -= (dx / dist) * semantic_attract
                        disp[u][1] -= (dy / dist) * semantic_attract
                        disp[v][0] += (dx / dist) * semantic_attract
                        disp[v][1] += (dy / dist) * semantic_attract

            # B. Calculate Explicit Edge Forces
            for u, v in edges_info:
                if u in nodes_info and v in nodes_info:
                    dx = pos[u][0] - pos[v][0]
                    dy = pos[u][1] - pos[v][1]
                    dist = math.hypot(dx, dy)
                    dist = max(dist, 0.01)

                    attract = (dist ** 2) / k

                    disp[u][0] -= (dx / dist) * attract
                    disp[u][1] -= (dy / dist) * attract
                    disp[v][0] += (dx / dist) * attract
                    disp[v][1] += (dy / dist) * attract

            # C. Apply Displacements
            for n in nodes:
                dx, dy = disp[n]
                dist = math.hypot(dx, dy)
                if dist > 0:
                    pos[n][0] += (dx / dist) * min(abs(dx), t)
                    pos[n][1] += (dy / dist) * min(abs(dy), t)

            t *= (1.0 - i / iterations)

        # Final Rigid Body Anti-Overlap Pass
        for _ in range(10): 
            for idx_u in range(len(nodes)):
                for idx_v in range(idx_u + 1, len(nodes)):
                    u, v = nodes[idx_u], nodes[idx_v]
                    dx = pos[u][0] - pos[v][0]
                    dy = pos[u][1] - pos[v][1]
                    if dx == 0 and dy == 0: dx, dy = 1, 1

                    pad = 35 
                    min_x = (nodes_info[u]['width'] + nodes_info[v]['width']) / 2 + pad
                    min_y = (nodes_info[u]['height'] + nodes_info[v]['height']) / 2 + pad

                    if abs(dx) < min_x and abs(dy) < min_y:
                        overlap_x = min_x - abs(dx)
                        overlap_y = min_y - abs(dy)

                        if overlap_x < overlap_y:
                            push_x = (overlap_x / 2.0) * math.copysign(1, dx)
                            pos[u][0] += push_x
                            pos[v][0] -= push_x
                        else:
                            push_y = (overlap_y / 2.0) * math.copysign(1, dy)
                            pos[u][1] += push_y
                            pos[v][1] -= push_y

        # Format output for Top-Left rendering
        final_positions = {}
        for n in nodes:
            final_positions[n] = {
                'x': pos[n][0] - nodes_info[n]['width'] / 2,
                'y': pos[n][1] - nodes_info[n]['height'] / 2
            }

        return final_positions

    except Exception as e:
        print(f"Error in mathematical layout engine: {e}")
        return {}