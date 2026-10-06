            $frp_count = isset($data_results[$dc['data_id']]['total']) ? (int) $data_results[$dc['data_id']]['total'] : (int) $_ENV['end'];
            if ($frp_count < 3 && !headers_sent()) {
                header('X-Robots-Tag: noindex, follow', true);
                echo "<!-- c2 noindex -->";
            }
